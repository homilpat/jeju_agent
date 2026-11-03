# data/

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
