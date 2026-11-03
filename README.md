# JEJU LifePath AI (jeju_agent)

제주 공공데이터 기반 보행 경로·생활편의·지역 인프라 분석 AI Agent.
핵심 원칙: **하나의 보행도로 그래프 + 공통 도로구간(`edge_id`) 지표 + 하나의 통합 CA-MOR 경로 엔진.**

- 요구사항: [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)
- 아키텍처: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 구현 상태·게이트 기록: [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)
- 작업 로그: [HACKATHON_WORK_LOG.md](HACKATHON_WORK_LOG.md)

> **현재 단계: G0 완료 · G1 진행 중(미통과).** 데이터 수집·검증 파이프라인은 구현됐지만
> 공식 제주 데이터는 아직 확보·적용되지 않았다([data/README.md](data/README.md)). 테스트의 모든 그래프는
> `synthetic:` 출처의 **DEMO용 가상 데이터**다. 경로 탐색 서비스·Agent·UI는 미구현이다.

## 저장소 구조 (모노레포)

```text
src/
  backend/jeju_lifepath/      # Python 엔진 (설치 패키지)
    schemas/                  # RoadEdge, RouteRequest, RouteResult, 공통 enum/좌표 타입
    geo/                      # PedestrianGraph, geodesy, 분석기(Feature) 인터페이스
    routing/                  # 통행 제약 엔진(B01), CA-MOR 비용 기반(B02 cost term)
    data/                     # 데이터 레지스트리, CSV 커넥터, 좌표 정규화, ingest/G1 gate CLI
  frontend/                   # 지도·채팅 UI (G10, 미구현)
data/                         # raw/·processed/(git 제외), specs/, registry.json
tests/
  backend/{unit,fixtures}/    # 백엔드 테스트 + 가상 그래프 fixture
  frontend/                   # 프론트엔드 테스트 (G10, 미구현)
docs/
```

## 개발 환경 설치

요구: Python ≥ 3.11 (CPU만으로 동작, 외부 API 불필요).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # 의존성 버전은 pyproject.toml에 고정
cp .env.example .env               # G0는 키가 필요 없음. 실제 키는 절대 커밋 금지
```

## 테스트 및 정적 검사

```bash
python -m pytest                   # 전체 테스트 (tests/backend, tests/frontend)
python -m pytest tests/backend     # 백엔드만
ruff check .                       # lint
ruff format --check .              # 포맷 검사 (docs/ 는 제외)
```

## 데이터 원칙

- 실제 데이터와 가상 데이터는 `source_ids`로 구분한다. 테스트 fixture는 `synthetic:` 접두어와
  `DEMO` caveat을 반드시 가진다.
- `None` 지표는 "위험 0"이 아니라 "미확인"이다. 접근성·폐쇄 상태의 기본값은 `UNKNOWN`이다.
- API Key, 개인 위치, GPS 이력은 커밋하지 않는다 (`.env`는 `.gitignore` 대상).
