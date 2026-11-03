# 구현 상태 (Implementation Status)

기준 문서: REQUIREMENTS v3.1, ARCHITECTURE v3.1 · 최종 갱신: 2026-11-03

> **테스트 통과 ≠ 기능 완성.** 아래 "구현"은 *코드 계약과 기반 로직이 존재하고 가상
> 그래프에서 검증됨*을 뜻한다. 공식 제주 데이터로 검증된 기능은 아직 **없다**.

## 1. 게이트 현황

| Gate | 내용 | 상태 |
|---|---|---|
| **G0** | 저장소 구조·공통 스키마·기본 테스트 환경 | **통과** (아래 §4 검증 결과) |
| G1 | 제주 공식 데이터 확보·전처리 | **진행 중 · 미통과(블로커)** — 수집·검증 파이프라인 구현 완료, 공식 데이터 0건 (§6) |
| G2 | 보행도로 그래프 구축 (실데이터) | 미착수 (G0에 그래프 컨테이너만 존재) |
| G3 | 7종 분석기 | 미착수 (G0에 분석기 인터페이스만 존재) |
| G4 | 가중치 근거·민감도 | 미착수 — 코드에 실제 가중치 없음 |
| G5 | 도로통제·기상·이동약자 제약 | 기반만 구현 (규칙 2종, 실데이터 연동 없음) |
| G6 | CA-MOR 최적화 | 미착수 (비용 함수 기반만 구현, 최적화기·우회상한 탐색 없음) |
| G7~G12 | POI/Wi-Fi, 공백분석, Agent, UI, 통합검증, 제출 | 미착수 |

## 2. G0 구현 내역

| 모듈 | 책임 | 비고 |
|---|---|---|
| `schemas/common.py` | enum(PASS/BLOCK/UNKNOWN, OPEN/CLOSED/UNKNOWN, 경로선호, 이동프로필, 결과상태), `GeoPoint`, WGS84 LineString 타입 | CRS=EPSG:4326, geometry는 (lon, lat), 거리 m |
| `schemas/edge.py` | `RoadEdge` 공통 계약, `INDICATOR_FIELDS` 레지스트리 | 지표 [0,1]·NaN 거부, `None`=미확인, 상태 기본값 UNKNOWN, `source_ids` 필수, 불변(frozen) |
| `schemas/request.py` | `RouteRequest` | `route_preference` 기본값 없음(대화층이 질문), detour 0~0.50, tz-aware 시간 |
| `schemas/result.py` | `RouteResult` | 상태별 불변식: OK/UNVERIFIED는 경로 필수, 실패 상태는 경로 금지, OK 외 상태는 caveat 필수 |
| `geo/graph.py` | `PedestrianGraph` (edge_id 키 MultiDiGraph) | 중복 id·노드 좌표 불일치 거부, 길이/geometry 불일치 보고, 경로 연속성 검증, 허용 edge 집합 내 길이 최단경로(기준선), 입력 순서 무관 결정론 |
| `geo/features.py` | `EdgeFeatureAnalyzer` 프로토콜, `FeatureLayer`, `apply_feature_layers` | 존재하는 edge_id만 허용, 토폴로지 불변, 스키마 재검증, 기존 값 덮어쓰기 거부, 레이어별 provenance |
| `routing/constraints.py` | `ConstraintEngine` + `ClosureRule`, `AccessibilityRule`, 프로필 정책 | worst-of 결합, BLOCK은 어떤 탐색 집합에도 없음, verified-only는 PASS만 |
| `routing/cost.py` | `WeightSet`, `compute_edge_cost` | `length*(1+λ·q)`, 결측 재정규화 + coverage, 지표 방향은 WeightSet이 선언, 버전·근거·출처 필수 |
| `tests/backend/fixtures/synthetic_grid.py` | 6노드/14 directed edge 가상 그래프 | `synthetic:g0-toy-grid-v1` 출처 + DEMO caveat |

## 3. 요구사항·아키텍처 모순/누락 점검 및 G0 결정

| ID | 발견 사항 | G0 결정 (근거) | 후속 |
|---|---|---|---|
| C-1 | REQ §4.2 "BLOCK 구간 반드시 제외"와 §3 `accessibility`(계단·경사) BLOCK의 적용 대상이 불명확. 계단은 일반 보행자에게는 통행 가능. | 공식 폐쇄(CLOSED)는 모든 사용자 BLOCK. `accessibility`는 무단차가 필요한 프로필(휠체어·유모차·`avoid_stairs`·검증요청)에만 적용. | G5에서 기상·시간대 규칙 추가 |
| C-2 | 비용식 q는 "불리한 지표 결합"이나 CCTV·조명은 값이 클수록 유리, 유동인구는 목적별로 방향이 다름(REQ §5). 방향 정의 누락. | 지표 방향(ADVERSE/FAVOURABLE)을 edge가 아닌 `WeightSet`이 선언. 고립회피/혼잡회피를 같은 데이터로 표현 가능. | G4에서 방향·가중치 근거 문서화 |
| C-3 | ARCH `RouteRequest`(avoid_stairs, poi_categories, require_verified_accessibility)와 REQ §6 슬롯(constraints, requested_poi) 이름 불일치. `departure_time: str`, origin 튜플 축 순서 미정. | ARCH 필드명 채택(REQ 슬롯은 G9 대화층에서 매핑). 시간은 tz-aware datetime, 튜플은 (lat, lon)으로 고정하고 `GeoPoint`로 명시. | G9 슬롯→요청 매핑 |
| C-4 | `RoadEdge`는 `observed_at`·`resolution`이 하나뿐인데 지표별 출처·해상도(지점 CCTV vs 읍면동 범죄)가 다름. | edge의 `observed_at`은 입력 중 가장 오래된 시점(보수적), 지표별 시점·해상도는 `FeatureProvenance`에 보존. | 결과 설명 시 provenance 사용 |
| C-5 | REQ §4.1·4.9의 지오코딩 실패/스냅 실패/모호한 장소를 표현할 `RouteResult.status`가 없음. | 계약을 임의로 확장하지 않음. 현재는 DATA_UNAVAILABLE + caveat로만 표현 가능. | **결정 필요**(G6/G9): 상태 추가 여부 |
| C-6 | `RouteResult`에 미검증(UNKNOWN) 구간 목록 필드가 없음. | caveat 필수화로 UNVERIFIED 근거를 강제. | G6에서 필드 추가 검토 |
| C-7 | edge 방향성이 계약에 없음. | directed edge, 양방향 보도는 edge_id 2개(OSMnx walk 그래프와 동일). 두 방향의 지표는 G2/G3에서 일관 부여 필요. | G2 |
| C-8 | `quality.coverage`의 edge 단위 의미 미정의, 경고를 낼 coverage 임계값 미정의. 모든 지표 결측 시 q 정의 불가. | q=None, cost=length, coverage=0을 반환하여 상위 계층이 불확실성을 표시하도록 함. 임계값은 하드코딩하지 않음. | G4/G6에서 임계값 근거화 |
| C-9 | detour 상한 0.50은 ARCH의 "초기 정책", REQ에는 상한 없음. | `DETOUR_RATIO_POLICY_MAX` 상수 하나로 관리. | 정책 변경 시 한 곳만 수정 |
| C-10 | 게이트 명칭 차이: REQ G11 "통합 검증" vs ARCH G11 "tests"; ARCH G0 "schema+toy graph" vs 요청의 "제약·비용 기반 포함". | G0에 제약 엔진·비용 함수 **기반**까지 포함하되, G5/G6 완료로 간주하지 않음. | — |
| C-11 | ARCH §3은 `src/jeju_lifepath/` 단일 패키지, 사용자 요청은 모노레포(`src/backend`, `src/frontend`). | 사용자 요청 채택: `src/backend/jeju_lifepath`, `src/frontend`, `tests/backend`, `tests/frontend`. 패키지 내부 구조는 ARCH §3 유지. | — |

## 4. G0 검증 결과 (2026-11-03)

| 명령 | 결과 |
|---|---|
| `python -m pytest` | **107 passed**, 0 failed, 0 skipped (0.55s) |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 25 files already formatted |
| 새 venv에 비편집 설치(`pip install ".[dev]"`) 후 pytest | 107 passed (설치 재현 확인) |

테스트 분포: schemas 44 · graph 17 · constraints 23 · cost 15 · features 8.
범주: 정상(기준 최단경로, 병합, 비용식), 경계(지표 0/1, detour 0/0.50, λ=0), 실패(범위 밖 값,
축 뒤바뀐 좌표, 중복 id, 좌표 불일치, 알 수 없는 edge, 덮어쓰기 충돌), 회귀(입력 순서 무관 결정론,
BLOCK 경로 혼입 0건, verified-only에서 UNKNOWN 배제 후 경로 부재 판정).

## 5. 미구현 및 블로커

- **블로커(G1)**: 제주 공식 데이터 미확보. 개발 환경의 네트워크 정책이 제주데이터허브·공공데이터포털·OSM 접속을 거부(403). 도메인 허용 또는 원본 파일 제공 필요 (`docs/DATA_SOURCES.md` §3).
- **결정 필요**: C-5 (지오코딩·스냅 실패 상태 표현).
- 미구현: 데이터 레지스트리, OSM 그래프 구축·스냅, 7종 분석기, 가중치, CA-MOR 최적화기·우회상한 후보탐색, POI/Wi-Fi, 공백분석, Agent, UI.
- `frontend`·`tests/frontend`는 디렉터리만 존재 (G10).

## 6. G1 진행 상태 (2026-11-03) — 미통과

### 구현 (데이터 없이 가능한 부분)
| 모듈 | 책임 |
|---|---|
| `data/registry.py` | `DatasetSpec`(원본명·발행기관·URL·라이선스·수집일·기준일·origin·좌표계·인코딩·컬럼매핑), `DatasetRecord`(원본/가공 SHA-256, 통계), JSON 레지스트리, **G1 게이트 판정** |
| `data/connectors.py` | 로컬 CSV 커넥터: UTF-8(BOM)/CP949 엄격 디코딩, 헤더 중복·행 길이 오류 거부 |
| `data/normalize.py` | 좌표 정규화 → WGS84(lon, lat). pyproj로 TM 등 투영 좌표 변환. 결측·비수치·축 뒤바뀜 의심·범위 밖·중복 ID를 사유별로 거부(자동 보정 없음). 컬럼별 결측률, 제주 범위 내 비율 |
| `data/ingest.py` | 명세+원본 → 결정론적 GeoJSON + 레지스트리 기록. CLI `ingest`, `gate` |
| `geo/regions.py` | 제주도 대략 범위(마라도~추자도) — 좌표/CRS 오류 검출용, 행정경계 아님 |

### G1 게이트 판정 규칙 (`evaluate_g1_gate`)
레지스트리 내 최소 1개 데이터셋이 다음을 모두 만족해야 통과:
1. origin이 `OFFICIAL_JEJU` 또는 `OFFICIAL_NATIONAL` (SYNTHETIC/COMMUNITY 불가), https 출처 URL
2. 원본·가공 파일이 디스크에 있고 SHA-256이 기록과 일치 (변조·누락 검출)
3. 유효 행 ≥ 1
4. 제주 범위 내 좌표 비율 ≥ 0.95 (기본값, 인자로 조정 가능)

### 검증
- `python -m pytest` → **129 passed** (G0 107 + G1 파이프라인 22)
- `ruff check .` / `ruff format --check .` → 통과
- `python -m jeju_lifepath.data.ingest --registry data/registry.json gate` → `G1 NOT PASSED: no dataset registered` (exit 1) — **정상 동작이며 G1은 미통과**

### G1 통과에 남은 일
1. 공식 데이터 원본 1종 이상 확보 (네트워크 허용 또는 파일 제공)
2. 원문 페이지에서 라이선스·기준일 확인 후 `data/specs/*.json` 작성, ingest, gate 통과
3. 원본 커밋 가능 여부를 라이선스로 판단 (현재 `data/raw/`는 gitignore)
