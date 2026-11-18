"""무장애여행정보(허브·공공데이터포털 CSV + 허브 한글 문서)를 장소별로 묶어 data/private/barrier_free.json 으로 저장.

사용: uv run python scripts/build_barrier_free.py
먼저 받을 것: scripts/fetch_barrier_free.py(포털), 허브 "무장애" CSV·한글 문서(로그인 필요,
docs/DATA_AVAILABILITY.md §13, 목록 data/specs/barrier_free_hub_manifest.csv).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "backend"))

from jeju_lifepath.data.barrier_free import build  # noqa: E402
from jeju_lifepath.data.hwp import hwp_text  # noqa: E402

PRIVATE = ROOT / "data" / "private"


def main() -> None:
    places = build(PRIVATE / "barrier_free_hub", PRIVATE / "barrier_free", PRIVATE / "barrier_free_hub_files",
                   hwp_text, PRIVATE / "barrier_free_zip")
    out = PRIVATE / "barrier_free.json"
    out.write_text(json.dumps(places, ensure_ascii=False), encoding="utf-8")
    docs = sum(1 for p in places if p["doc"])
    tracks = sum(1 for p in places if p["track"])
    print(f"장소 {len(places)}곳(한글 문서 설명 {docs}곳, GPS 경로 {tracks}곳, "
          f"지점 {sum(len(p['points']) for p in places)}개) -> data/private/barrier_free.json")


if __name__ == "__main__":
    main()
