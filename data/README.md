# data/

## 제출 ZIP에 들어 있는 가공 자료

서버가 실행할 때 읽는 파일만 넣었어요(`scripts/make_submission.py`의 `DATA_FILES`). 원본 내려받기 파일(허브 한글 문서·압축 4GB 등)과 회원·대화 기록(`app.sqlite3`)은 넣지 않았어요.

| 파일 | 원자료(출처) | 이용조건 | 가공 |
|---|---|---|---|
| `private/osm/jeju_highways.json` | OpenStreetMap 제주 도로망 | ODbL 1.0. © OpenStreetMap contributors. 이 가공본도 ODbL로 배포 | 제주 범위의 길만 추출 |
| `private/barrier_free.json` | 제주데이터허브 무장애여행정보 272건(허브 622~742, 858, 1050~1200) | 제주데이터허브 이용약관(출처 표시) | 한글 문서 본문·GPS CSV를 장소 이름으로 묶어 182곳 |
| `private/CCTV정보.csv` | 제주데이터허브 510 CCTV 정보 | 제주데이터허브 이용약관 | 원본 그대로 |
| `private/CCTV정보_제주특별자치도.csv` | 행정안전부 전국CCTV표준데이터(공공데이터포털) | 공공데이터포털 이용허락(출처 표시) | 제주만 |
| `private/공중화장실정보_제주특별자치도.csv` | 전국공중화장실표준데이터(공공데이터포털) | 공공데이터포털 이용허락(출처 표시) | 제주만 |
| `private/hub_link/*.csv` | 허브 1063·1165·1058·1236(공공데이터포털 연결) | 이용허락 제한 없음 | 원본 그대로 |
| `processed/accident-zones.geojson` | 한국도로교통공단 교통사고 다발지역(공공데이터포털) | 공공데이터포털 이용허락(출처 표시) | 제주 영역 도형 |
| `processed/hub-accident-hotspots.geojson`, `processed/hub-wifi.geojson` | 제주데이터허브 교통사고 다발지역(493)·공공와이파이 AP 정보(720) | 제주데이터허브 이용약관 | 좌표 정규화(WGS84) |
| `processed/jeju-admdong.geojson` | 통계청 SGIS 행정동 경계, 가공 vuski/admdongkor | CC BY 4.0(출처 표시) | 제주 43개 읍면동만 |

### 빠진 자료: 생활안전지도 경찰관서

공공누리 4유형(변경 금지)이라 형식을 바꾼 파일을 다시 배포하지 않아요. 없으면 "근처 파출소"만 안 돼요. 직접 받으려면:

1. [생활안전지도 오픈API](https://www.safemap.go.kr/)에서 회원가입 → 오픈API 신청 → 인증키를 받아 `.env`의 `SAFEMAP_API_KEY`에 넣어요.
2. `python scripts/fetch_jeju_data.py --police-only` → `data/private/raw/safemap_police.csv`(제주만)
3. `python -m jeju_lifepath.data.ingest --registry data/registry.json ingest data/specs/safemap_police.json data/private/raw/safemap_police.csv` → `data/processed/safemap-police.geojson`
4. 서버를 다시 켜요.


| 경로 | 내용 | Git |
|---|---|---|
| `raw/` | 내려받은 원본 파일 (라이선스 확인 전까지 커밋하지 않음) | 무시 |
| `processed/` | 정규화된 GeoJSON (WGS84, 결정론적 직렬화) | 무시 |
| `specs/` | 데이터셋 메타데이터 명세(JSON) | 커밋 |
| `registry.json` | 레지스트리: 출처·라이선스·기준일·해시·컬럼매핑·좌표계·결측률 | 커밋 |

## 공식 데이터 1종 등록 절차 (G1)

1. 제주데이터허브(https://www.jejudatahub.net/) 또는 공공데이터포털(https://www.data.go.kr/)에서
   CSV 원본을 내려받아 `data/raw/`에 둔다. 파일을 편집하지 않는다(해시가 증빙이다).
2. 데이터 페이지에서 원본명·제공기관·URL·라이선스·기준일을 확인해 `data/specs/<dataset_id>.json` 작성:

```json
{
  "dataset_id": "jeju-example-points",
  "title": "<데이터 페이지의 원본명 그대로>",
  "publisher": "<제공기관>",
  "source_url": "https://<데이터 페이지 URL>",
  "license": "<이용허락 조건, 예: 공공누리 제1유형>",
  "collected_on": "2026-11-03",
  "reference_date": "<데이터 기준일 YYYY-MM-DD 또는 null>",
  "origin": "OFFICIAL_JEJU",
  "crs": "EPSG:4326",
  "encoding": null,
  "columns": {
    "x_column": "<경도 또는 X 좌표 컬럼명>",
    "y_column": "<위도 또는 Y 좌표 컬럼명>",
    "id_column": "<관리번호 컬럼명 또는 null>",
    "attributes": {"name": "<시설명 컬럼명>"}
  }
}
```

3. 실행:

```bash
python -m jeju_lifepath.data.ingest --registry data/registry.json ingest data/specs/<id>.json data/raw/<file>.csv
python -m jeju_lifepath.data.ingest --registry data/registry.json gate   # exit 0 = G1 통과
```

`origin`이 `SYNTHETIC`/`COMMUNITY`인 데이터, 해시가 바뀐 파일, 제주 범위 밖 좌표가 많은 데이터는
게이트를 통과하지 못한다. 좌표계가 TM(예: EPSG:5186)이면 `crs`에 그대로 기재하면 WGS84로 변환된다.
