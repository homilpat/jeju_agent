"""교통사고 다발구역(영역 도형)을 받아 data/processed/accident-zones.geojson 으로 저장.

사용: uv run python scripts/fetch_accident_zones.py   (키: .env 의 DATA_GO_KR_API_KEY)
- 한국도로교통공단 지자체별 교통사고 다발지역(전체 사고, 선정 연도별): 제주시·서귀포시
- 전국교통사고다발지역표준데이터(보행노인·보행어린이·스쿨존어린이·자전거): 제주만 골라냄
docs/DATA_AVAILABILITY.md §8 참고. 오류 문구에 주소를 넣지 않는다(키가 들어 있음).
"""

import json
import urllib.parse
import urllib.request
from pathlib import Path

from check_api_keys import ENV_FILE, load_env
from pyproj import Transformer

OUT = Path(__file__).resolve().parents[1] / "data" / "processed" / "accident-zones.geojson"
TAAS = "https://apis.data.go.kr/B552061/frequentzoneLg/getRestFrequentzoneLg?"
STANDARD = "https://api.data.go.kr/openapi/tn_pubr_public_acdnt_area_api?"
JEJU_GUGUN = {"110": "제주시", "130": "서귀포시"}  # 도로교통공단 시군구 코드(시도 50)
# 표준데이터 영역 좌표는 UTM-K(EPSG:5179) 숫자로 온다(정읍 예시 좌표로 확인)
NODATA = "03"  # 두 API 모두 자료 없음에 쓰는 결과 코드(응답으로 확인)
TAAS_FIRST_YEAR = 2012
_FROM_UTMK = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)


def get(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)
    except Exception as e:
        raise RuntimeError(f"요청 실패: {type(e).__name__}") from None


def taas_items(key: str, year: int, gugun: str) -> list[dict]:
    """한 해·한 시의 전체 쪽. 자료 없음은 빈 목록, 그 밖의 결과 코드는 오류."""
    items, page = [], 1
    while True:
        q = urllib.parse.urlencode(
            {
                "serviceKey": key,
                "searchYearCd": str(year),
                "siDo": "50",
                "guGun": gugun,
                "type": "json",
                "numOfRows": 100,
                "pageNo": page,
            }
        )
        d = get(TAAS + q)
        if d.get("resultCode") == NODATA:
            return items
        if d.get("resultCode") != "00":
            raise RuntimeError(f"도로교통공단 응답 오류(결과 코드 {d.get('resultCode')})")
        items += (d.get("items") or {}).get("item") or []
        if len(items) >= int(d.get("totalCount") or 0):
            return items
        page += 1


def taas_zones(key: str) -> list[dict]:
    feats = []
    year, seen_data = TAAS_FIRST_YEAR, False
    while True:  # 자료가 처음 나온 뒤 다시 빈 해가 나오면 그 앞 해까지가 최신
        got = 0
        for gugun in JEJU_GUGUN:
            for it in taas_items(key, year, gugun):
                got += 1
                feats.append(
                    {
                        "type": "Feature",
                        "geometry": json.loads(it["geom_json"]),
                        "properties": {
                            "source": "도로교통공단 지자체별 교통사고 다발지역",
                            "year": year,
                            "kind": "전체 사고",
                            "name": it["spot_nm"],
                            "accidents": it["occrrnc_cnt"],
                            "casualties": it["caslt_cnt"],
                        },
                    }
                )
        if got:
            seen_data = True
        elif seen_data:
            return feats
        if year > TAAS_FIRST_YEAR + 30:
            raise RuntimeError("도로교통공단 자료 연도를 찾지 못했습니다.")
        year += 1


def standard_zones(key: str) -> list[dict]:
    feats, page = [], 1
    while True:
        q = urllib.parse.urlencode(
            {"serviceKey": key, "type": "json", "pageNo": page, "numOfRows": 1000}
        )
        d = get(STANDARD + q)
        code = (d.get("header") or {}).get("resultCode")
        if code == NODATA and page > 1:
            return feats  # 마지막 쪽을 지남
        if code != "00" or not d.get("body"):
            raise RuntimeError(f"표준데이터 응답 오류(결과 코드 {code})")
        body = d["body"]
        items = body["items"]["item"] if isinstance(body["items"], dict) else body["items"]
        if not items:
            return feats
        for it in items:
            if not it["ctprvnSignguNm"].startswith("제주"):
                continue
            raw = it[
                "acdntMlttdPynInfo"
            ]  # "{ type: Polygon, coordinates: [...] }" (키에 따옴표 없음)
            rings = json.loads(raw[raw.index("[") : raw.rindex("]") + 1])
            rings = [[list(_FROM_UTMK.transform(x, y)) for x, y in ring] for ring in rings]
            feats.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": rings},
                    "properties": {
                        "source": "전국교통사고다발지역표준데이터",
                        "year": int(it["acdntYear"]),
                        "kind": it["acdntTypeSe"],
                        "name": it["acdntAreaLcNm"],
                        "accidents": int(it["occrrncCo"]),
                        "casualties": int(it["casltCo"]),
                    },
                }
            )
        page += 1


def main() -> None:
    key = load_env(ENV_FILE)["DATA_GO_KR_API_KEY"]
    feats = taas_zones(key) + standard_zones(key)
    if not feats:
        raise SystemExit("받은 구역이 없어 저장하지 않았습니다.")
    for i, f in enumerate(feats):
        f["id"] = str(i)
    if not any(f["properties"]["kind"] == "전체 사고" for f in feats) or len(feats) == sum(
        f["properties"]["kind"] == "전체 사고" for f in feats
    ):
        raise SystemExit("두 자료 중 하나가 비어 있어 저장하지 않았습니다.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")  # 다 쓴 뒤에 바꿔 넣어, 중간에 실패해도 기존 파일이 남게
    tmp.write_text(
        json.dumps(
            {"type": "FeatureCollection", "dataset_id": "accident_zones", "features": feats},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tmp.replace(OUT)
    kinds = {}
    for f in feats:
        kinds[f["properties"]["kind"]] = kinds.get(f["properties"]["kind"], 0) + 1
    print(f"{len(feats)}개 구역 저장: {kinds}")


if __name__ == "__main__":
    main()
