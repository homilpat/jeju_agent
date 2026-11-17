"""Check that the API keys in .env actually work. Never prints key values.

Usage: python scripts/check_api_keys.py
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
SAFEMAP = "https://www.safemap.go.kr/openapi2/"
JEJU_HUB = "https://open.jejudatahub.net/api/proxy/"
# 허브 데이터ID는 .env에서 읽는다 (공개 여부가 불분명해 비밀값으로 취급)
HUB_DATA_ID_VARS = {
    "공공와이파이(720)": "JEJU_HUB_WIFI_DATA_ID",
    "약국(495)": "JEJU_HUB_PHARMACY_DATA_ID",
    "교통사고 다발지역(493)": "JEJU_HUB_ACCIDENT_DATA_ID",
}


def load_env(path: Path) -> dict[str, str]:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return {**env, **{k: v for k, v in os.environ.items() if k in env and v}}


STATUS_FIELDS = (
    "resultCode", "resultMsg", "errMsg", "returnAuthMsg", "message", "msg", "errorType",
)
COUNT_FIELDS = ("totCnt", "totalCount", "total_count")


def path_part(value: str) -> str:
    """URL 경로에 넣을 값. 공백 등이 섞여도 잘못된 URL 예외(키가 담긴 traceback)가 나지 않게."""
    return urllib.parse.quote(value, safe="")


def fetch(url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", errors="replace")
        except Exception:  # 본문 읽기 실패도 URL을 흘리지 않게
            return e.code, ""
    except Exception as e:  # 예외 메시지·traceback에 키가 담긴 URL이 들어갈 수 있다
        return 0, f"요청 실패({type(e).__name__})"


def redact(text: str, secrets: list[str]) -> str:
    for s in filter(None, secrets):
        for form in {s, urllib.parse.quote(s), urllib.parse.quote(s, safe=""),
                     urllib.parse.quote_plus(s), json.dumps(s)[1:-1], s.replace("/", "\\/")}:
            # 퍼센트 인코딩은 대소문자(%2F / %2f) 모두 나올 수 있다
            text = re.sub(re.escape(form), "<key>", text, flags=re.IGNORECASE)
    return text


def _find(data: object, fields: tuple[str, ...], out: dict) -> dict:
    if isinstance(data, dict):
        for k, v in data.items():
            if k in fields and not isinstance(v, dict | list):
                out.setdefault(k, v)
            _find(v, fields, out)
    elif isinstance(data, list):
        for v in data:
            _find(v, fields, out)
    return out


def summarize(status: int, body: str, secrets: list[str]) -> str:
    """상태·오류 문구·건수만 보여준다. 응답 본문(장소명·주소 등)은 출력하지 않는다
    (카카오 결과 저장 금지: 출력이 로그 파일로 남을 수 있음)."""
    body = redact(body, secrets)
    try:
        data = json.loads(body)
    except ValueError:
        return redact(f"HTTP {status} | {' '.join(body.split())[:120]}", secrets)
    info = _find(data, STATUS_FIELDS + COUNT_FIELDS, {})
    if isinstance(data, dict) and isinstance(data.get("documents"), list):
        info["documents"] = len(data["documents"])
    return redact(f"HTTP {status} | {json.dumps(info, ensure_ascii=False)}", secrets)


def main() -> None:
    env = load_env(ENV_FILE)
    names = ("SAFEMAP_API_KEY", "DATA_GO_KR_API_KEY", "KAKAO_REST_API_KEY", "JEJU_DATAHUB_API_KEY")
    sm, dg, kr, jh = (env.get(k, "") for k in names)
    q = urllib.parse.urlencode
    checks = []
    # 생활안전지도: 치안시설·횡단보도·인도는 데이터, 범죄주의구간·보안등은 그림 전용 표시
    for code, name in [("IF_0036", "치안시설"), ("IF_0097", "횡단보도"), ("IF_0095", "인도"),
                       ("IF_0087", "범죄주의구간(전체)"), ("IF_0102", "보안등")]:
        params = {"serviceKey": sm, "pageNo": 1, "numOfRows": 2, "returnType": "json"}
        checks.append((f"생활안전지도 {name}", SAFEMAP + code + "?" + q(params), None))
    checks.append(("카카오 장소 검색", "https://dapi.kakao.com/v2/local/search/keyword.json?"
                   + q({"query": "제주국제공항", "size": 1}), {"Authorization": f"KakaoAK {kr}"}))
    checks.append(("공공데이터포털 횡단보도",
                   "https://api.data.go.kr/openapi/tn_pubr_public_crosswalk_api?"
                   + q({"serviceKey": dg, "pageNo": 1, "numOfRows": 2, "type": "json"}), None))
    hub_ids = {name: env.get(var, "") for name, var in HUB_DATA_ID_VARS.items()}
    for name, data_id in hub_ids.items():
        url = f"{JEJU_HUB}{path_part(data_id)}/{path_part(jh)}?" + q({"limit": 2})
        checks.append((f"제주데이터허브 {name}", url, None))
    for name, url, headers in checks:
        print(f"[{name}] {summarize(*fetch(url, headers), [sm, dg, kr, jh, *hub_ids.values()])}")


if __name__ == "__main__":
    main()
