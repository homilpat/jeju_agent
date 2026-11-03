# HACKATHON WORK LOG — JEJU LifePath AI

제출 마감: 2026-11-20 10:00 KST. 각 게이트마다 변경 파일·테스트 결과·미해결 블로커를 기록한다
(REQUIREMENTS §9).

---

## 2026-11-03 · G0 저장소·스키마·기본 테스트 환경 — **통과**

### 시작 상태
- 저장소에는 `README.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`만 존재. 기존 코드 없음 → 재구성 대상 없음.

### 수행
1. 요구사항·아키텍처 교차 점검 → 모순/누락 11건 기록 (`docs/IMPLEMENTATION_STATUS.md` §3).
2. 모노레포 구조 채택(사용자 요청): `src/backend`, `src/frontend`, `tests/backend`, `tests/frontend`.
3. `pyproject.toml`: 의존성 고정(pydantic 2.14.2, networkx 3.7, shapely 2.2.0; dev: pytest 9.2.0, ruff 0.17.3), pytest·Ruff 설정.
4. 공통 스키마 `RoadEdge`/`RouteRequest`/`RouteResult` (Pydantic, 불변, extra 금지).
5. `PedestrianGraph`, 분석기 인터페이스(`EdgeFeatureAnalyzer`/`FeatureLayer`), 통행 제약 엔진, 비용 함수 기반.
6. 가상 그래프 fixture(`synthetic:g0-toy-grid-v1`, DEMO caveat) 및 단위 테스트 107건.
7. `.gitignore`(.env, 원시/개인 데이터), `.env.example`(빈 placeholder), README.

### 변경 파일
- 신규: `pyproject.toml`, `.gitignore`, `.env.example`, `HACKATHON_WORK_LOG.md`, `docs/IMPLEMENTATION_STATUS.md`
- 신규: `src/backend/jeju_lifepath/{__init__,schemas/*,geo/*,routing/*}.py`, `src/frontend/README.md`
- 신규: `tests/backend/{fixtures,unit}/*.py`, `tests/frontend/README.md`
- 수정: `README.md`

### 검증
```
python -m pytest        -> 107 passed in 0.55s
ruff check .            -> All checks passed!
ruff format --check .   -> 25 files already formatted
fresh venv install + pytest -> 107 passed
```
중간 실패와 처리: 1차 Ruff 검사에서 E501 3건, RUF007 3건(`zip(x, x[1:])` → `itertools.pairwise`),
RUF100 2건 발견 → 코드 수정으로 해결. Ruff formatter가 `docs/*.md` 코드블록을 재작성하려 해
기준 문서 보호를 위해 `docs`를 Ruff 대상에서 제외(문서 내용 변경 없음).

### 미해결 / 블로커
- G1: 공식 제주 데이터 미확보(블로커). 이번 작업에서 공식 데이터 사용을 주장하지 않음.
- C-5: 지오코딩/스냅 실패 상태 표현 방식 결정 필요.

### 다음 단계
- G1 진행 가능 (G0 검증 통과). G1에서 데이터 레지스트리와 원본 확보·해시·라이선스 기록부터 시작.

---

## 2026-11-03 · G1 제주 공식 데이터 확보·전처리 — **진행 중 / 미통과**

### 시도
- 공식 데이터 접근 확인: `www.jejudatahub.net`, `www.data.go.kr`, `apis.data.go.kr`, `api.odcloud.kr`,
  OSM 계열(`openstreetmap.org`, `overpass-api.de`, `download.geofabrik.de`) 모두 개발 환경 프록시에서
  **CONNECT 403 (네트워크 정책 거부)**. 데이터를 확보하지 못함 → 가상 데이터로 대체하지 않음.

### 수행 (데이터 비의존 부분)
- 데이터 레지스트리, 로컬 CSV 커넥터(UTF-8/CP949), 좌표 정규화(pyproj, TM→WGS84), 결정론적 GeoJSON 산출,
  해시 기반 G1 게이트 판정, CLI(`ingest`, `gate`).
- 의존성 추가: `pyproj==3.8.0` (오프라인 좌표 변환).

### 변경 파일
- 신규: `src/backend/jeju_lifepath/data/{__init__,registry,connectors,normalize,ingest}.py`, `src/backend/jeju_lifepath/geo/regions.py`
- 신규: `tests/backend/unit/test_data_pipeline.py`, `data/README.md`, `data/specs/.gitkeep`, `docs/DATA_SOURCES.md`
- 수정: `pyproject.toml`, `.gitignore`(`data/processed/`), `docs/IMPLEMENTATION_STATUS.md`, `README.md`

### 검증
```
python -m pytest        -> 129 passed
ruff check .            -> All checks passed!
ruff format --check .   -> 35 files already formatted
gate CLI                -> G1 NOT PASSED: no dataset registered (exit 1)
```
중간 실패: Ruff PT018(복합 assert) 2건 → assert 분리로 수정.

### 블로커
- **공식 데이터 미확보** (네트워크 정책). 해결: 환경 Network access에 위 도메인 허용, 또는 원본 CSV 제공.
- G2 이후 단계는 G1 통과 전까지 진행하지 않음.
