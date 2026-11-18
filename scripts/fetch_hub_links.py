"""제주데이터허브에 LINK로 등록된 자료(원천: 공공데이터포털 파일)를 data/private/hub_link/ 에 받는다.

사용: uv run python scripts/fetch_hub_links.py   (키 필요 없음, 공공데이터포털 파일 내려받기는 로그인 없이 됨)
허브 목록(제주데이터허브 데이터목록_20261105.xlsx)의 번호와 공공데이터포털 자료 번호(publicDataPk)를 짝지어 둔다.
이용허락범위: 네 자료 모두 "제한 없음"(공공데이터포털 자료 페이지, 2026-11-19 확인). docs/DATA_AVAILABILITY.md §11.
"""

import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "private" / "hub_link"
PORTAL = "https://www.data.go.kr"
# 저장 이름: (허브 번호, 공공데이터포털 자료 번호, 자료 이름)
DATASETS = {
    "night_pharmacy": (1063, "15043697", "제주특별자치도_공공심야약국현황"),
    "nursing_room": (1165, "15045348", "제주특별자치도_모유수유시설설치현황"),
    "emergency": (1058, "15056000", "제주특별자치도_응급의료기관현황"),
    "olle": (1236, "15043496", "제주특별자치도_올레코스현황"),
}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def download(public_pk: str) -> bytes:
    """공공데이터포털 파일 자료의 첫 파일(CSV, cp949)."""
    raw = get(f"{PORTAL}/tcs/dss/selectFileDataDownload.do?publicDataPk={public_pk}&fileDetailSn=1&dataNm=")
    # 응답 모양이 자료마다 달라(맨 위 또는 안쪽) 첫 번째로 값이 있는 atchFileId를 쓴다
    atch = next(iter(re.findall(r'"atchFileId":"([^"]+)"', raw.decode("utf-8", "replace"))), None)
    if not atch:
        raise RuntimeError(f"{public_pk}: 내려받기 번호를 찾지 못함(포털 화면이 바뀌었을 수 있음)")
    body = get(f"{PORTAL}/cmm/cmm/fileDownload.do?atchFileId={atch}&fileDetailSn=1")
    body.decode("cp949")  # CSV가 아니면(오류 화면 등) 여기서 실패해 기존 파일을 덮지 않는다
    return body


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (hub_no, public_pk, title) in DATASETS.items():
        body = download(public_pk)
        (OUT / f"{name}.csv").write_bytes(body)
        rows = body.decode("cp949").count("\n") - 1
        print(f"허브 {hub_no} {title}: {rows}행 -> data/private/hub_link/{name}.csv")


if __name__ == "__main__":
    main()
