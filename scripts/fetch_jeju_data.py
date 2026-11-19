"""Download Jeju point datasets into data/private/raw/*.csv (keys and data IDs come from .env).

Usage: python scripts/fetch_jeju_data.py
Then ingest each CSV with its spec in data/specs/ (python -m jeju_lifepath.data.ingest ...).
"""

import csv
import json
import time
import sys
import urllib.parse
from http import HTTPStatus
from pathlib import Path

from check_api_keys import ENV_FILE, JEJU_HUB, SAFEMAP, fetch, load_env, path_part

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "private" / "raw"
JEJU_SIDO_CODE = "50"  # 행정표준코드 제주특별자치도
CLOSED_WORDS = ("폐업", "휴업", "취소")
HUB_MAX_PASSES = 20  # shortcut: 근거 없는 상한, 수렴 안 하면 오류로 알려줌
# shortcut: 허브 하루 한도(약 700회에서 막힘, 정확한 값 미확인)보다 낮게 잡은
# 1회 실행 전체 HTTP 호출 상한(재시도·모든 자료 포함)
CALL_BUDGET = 400
_calls = 0
HUB_PAGE = 100


def get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    global _calls  # noqa: PLW0603 - 실행 전체 호출 수
    for attempt in range(3):
        _calls += 1
        if _calls > CALL_BUDGET:
            raise RuntimeError(f"이번 실행 호출 상한 {CALL_BUDGET}회 초과")
        status, body = fetch(url, headers)
        if status == HTTPStatus.OK:
            try:
                return json.loads(body)
            except ValueError:
                raise RuntimeError("응답이 JSON이 아님") from None
        time.sleep(2 * (attempt + 1))
    # never put the URL in the error: it carries the key
    raise RuntimeError(f"request failed with HTTP {status}")


def hub_base(env: dict[str, str], id_var: str) -> str:
    return f"{JEJU_HUB}{path_part(env[id_var])}/{path_part(env['JEJU_DATAHUB_API_KEY'])}?"


def hub_rows(base: str, params: dict | None = None, *, allow_incomplete: bool = False,
             get=get_json) -> list[dict]:
    """허브는 페이지 순서가 호출마다 달라 겹치거나 빠지는 줄이 생긴다.
    여러 번 받아 합치고, 전체 건수에 차면 끝낸다. 두 번 연속 새 줄이 없는데도 모자라면
    allow_incomplete일 때만 경고와 함께 돌려주고, 아니면 실패한다(누락을 정상으로 저장하지 않음)."""
    unique: dict[str, dict] = {}
    total, quiet = 0, 0
    for _ in range(HUB_MAX_PASSES):
        before, page = len(unique), 1
        while True:
            query = urllib.parse.urlencode({**(params or {}), "limit": HUB_PAGE, "number": page})
            data = get(base + query)
            if not isinstance(data.get("totCnt"), int) or not isinstance(data.get("data"), list):
                raise RuntimeError("허브 응답 형식이 다름 (한도 초과·오류 응답일 수 있음)")
            total = data["totCnt"]
            for r in data["data"]:
                unique[json.dumps(r, sort_keys=True, ensure_ascii=False)] = r
            # 페이지 번호를 무시하거나 hasMore가 끝나지 않아도 마지막 페이지 다음에서 멈춘다
            if not data.get("hasMore") or page * HUB_PAGE >= total:
                break
            page += 1
        if len(unique) >= total:
            return list(unique.values())
        quiet = quiet + 1 if len(unique) == before else 0
        if quiet >= 2:  # noqa: PLR2004
            if not allow_incomplete:
                break
            print(f"  경고: {total}건 중 겹치지 않는 줄 {len(unique)}건만 확보 (불완전)")
            return list(unique.values())
    raise RuntimeError(f"{total}건 중 {len(unique)}건만 확보 (불완전)")


def hub_rows_first_page(env: dict[str, str]) -> list[dict]:
    query = urllib.parse.urlencode({"limit": HUB_PAGE, "number": 1})
    data = get_json(hub_base(env, "JEJU_HUB_WIFI_DATA_ID") + query).get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError("허브 응답 형식이 다름 (한도 초과·오류 응답일 수 있음)")
    return data


def write_csv(name: str, rows: list[dict]) -> None:
    if not rows:  # 오류 응답을 빈 자료로 받아 기존 파일을 지우지 않게
        raise RuntimeError(f"{name}: 받은 자료가 없어 저장하지 않음")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for r in rows for k in r})  # 열 순서도 고정해야 같은 자료 → 같은 해시
    with (RAW_DIR / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        # API 응답 순서가 매번 달라 정렬해야 같은 자료 → 같은 파일(해시)
        writer.writerows(sorted(rows, key=lambda r: [str(r.get(k, "")) for k in fields]))
    print(f"{name}: {len(rows)}건")


def main() -> None:
    env = load_env(ENV_FILE)
    if "--police-only" not in sys.argv:  # 제출 ZIP에는 경찰관서만 빠져 있어 그것만 받을 때(SAFEMAP_API_KEY만 필요)
        fetch_hub(env)
    fetch_police(env)


def fetch_hub(env: dict) -> None:
    write_csv("hub_accident_hotspots", hub_rows(hub_base(env, "JEJU_HUB_ACCIDENT_DATA_ID")))

    # 와이파이는 날짜별 스냅샷이 쌓여 있어 가장 최근 날짜 하나만 받는다
    latest = max(r["baseDate"] for r in hub_rows_first_page(env))
    write_csv("hub_wifi", hub_rows(
        hub_base(env, "JEJU_HUB_WIFI_DATA_ID"), {"baseDate": latest}, allow_incomplete=True
    ))

    pharmacies = [
        p for p in hub_rows(hub_base(env, "JEJU_HUB_PHARMACY_DATA_ID"))
        if not any(w in str(p.get("openStatus", "")) for w in CLOSED_WORDS)
    ]
    # 카카오 로컬 API 결과는 저장 금지(운영정책) → 주소만 저장, 좌표는 공공 지오코더로
    write_csv("hub_pharmacies", pharmacies)


def fetch_police(env: dict) -> None:
    items, page = [], 1
    while True:  # 한 번에 최대 1,000건
        url = SAFEMAP + "IF_0036?" + urllib.parse.urlencode(
            {"serviceKey": env["SAFEMAP_API_KEY"], "pageNo": page, "numOfRows": 1000,
             "returnType": "json"}
        )
        body = get_json(url).get("body") or {}
        got = (body.get("items") or {}).get("item") or []
        if not got:  # 오류 응답이나 빈 페이지에서 끝없이 돌지 않게
            raise RuntimeError(f"치안시설 {page}쪽 응답이 비어 있음")
        items += got
        if len(items) >= body.get("totalCount", 0):
            break
        page += 1
    write_csv("safemap_police", [i for i in items if str(i.get("ctprvn_cd")) == JEJU_SIDO_CODE])


if __name__ == "__main__":
    main()
