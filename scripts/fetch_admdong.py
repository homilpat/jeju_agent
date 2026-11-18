"""제주 행정동(읍면동) 경계를 받아 data/processed/jeju-admdong.geojson 으로 저장.

사용: uv run python scripts/fetch_admdong.py   (키 필요 없음)
원자료: 통계청 SGIS 행정동 경계(공공누리 제1유형), 가공: vuski/admdongkor ver20261001, CC BY 4.0.
출처 표기(LICENSE-DATA §3): "본 데이터는 통계청 통계지리정보서비스(SGIS, https://sgis.kostat.go.kr)에서
공공누리 제1유형으로 개방한 행정동 경계를 가공한 것이며(가공: vuski/admdongkor,
https://github.com/vuski/admdongkor), CC BY 4.0으로 배포됩니다."
지역 공백 분석(REQUIREMENTS §2 E01)에서 사용자가 말한 읍면동의 범위로만 쓴다. docs/DATA_AVAILABILITY.md §10.
"""

import hashlib
import json
import urllib.request
from pathlib import Path

URL = (
    "https://raw.githubusercontent.com/vuski/admdongkor/master/"
    "ver20261001/HangJeongDong_ver20261001.geojson"
)
# 2026-11-18에 받은 원본 파일의 SHA-256. 다르면 원본이 바뀐 것이라 멈춘다
SHA256 = "843ece3a0b3429742cd4a1b699cc13987a88b5e5401f382e7fdaaa194ea5e605"
JEJU_SIDO = "50"  # 행정동 코드의 시도 번호(제주특별자치도)
OUT = Path(__file__).resolve().parents[1] / "data" / "processed" / "jeju-admdong.geojson"
SOURCE = "통계청 SGIS 행정동 경계(공공누리 제1유형) 가공: vuski/admdongkor ver20261001, CC BY 4.0"


def main() -> None:
    with urllib.request.urlopen(URL, timeout=300) as r:
        raw = r.read()
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise SystemExit("원본 파일 내용이 기록과 달라 저장하지 않았어요. 버전을 확인해 주세요.")
    data = json.loads(raw.decode("utf-8"))
    features = [
        {
            "type": "Feature",
            "properties": {
                "name": f["properties"]["adm_nm"].split()[-1],
                "full_name": f["properties"]["adm_nm"],
                "code": f["properties"]["adm_cd2"],
            },
            "geometry": f["geometry"],
        }
        for f in data["features"]
        if f["properties"]["sido"] == JEJU_SIDO
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {"type": "FeatureCollection", "dataset_id": "jeju-admdong", "source": SOURCE,
             "features": features},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"읍면동 {len(features)}곳 저장: data/processed/{OUT.name}")


if __name__ == "__main__":
    main()
