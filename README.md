# 설문대할망 AI 길잡이 (JEJU LifePath AI)

제주데이터허브 자료로 걷는 여행자와 주민에게 "걸어서 갈 수 있는 길·쉴 곳·도움받을 곳"을 대화 한 번으로 찾아 주는 AI Agent.
LLM이 목표를 보고 도구를 스스로 골라 부르고, 결과를 보며 다시 계획한다. 계산은 보행망 엔진과 지식그래프가 실제 데이터로만 한다.

- 기획서: [docs/PROPOSAL.md](docs/PROPOSAL.md) (Word: `docs/PROPOSAL.docx`)
- 운영·구조: [docs/AGENT_RUNBOOK.md](docs/AGENT_RUNBOOK.md)
- 데이터 출처·전처리: [docs/DATA_AVAILABILITY.md](docs/DATA_AVAILABILITY.md)
- 검증 결과: [docs/VALIDATION_REPORT.md](docs/VALIDATION_REPORT.md)
- 정한 값과 근거: [docs/development/open-decisions.md](docs/development/open-decisions.md)

## 제출물 안내 (대회 요구 항목 → 위치)

| 요구 항목 | 위치 |
|---|---|
| Agent 실행 코드 | `src/backend/jeju_lifepath/agent/` (루프 `loop.py`, 오케스트레이터 `orchestrator.py`, 지식그래프 `kg.py`) |
| Prompt | `src/backend/jeju_lifepath/agent/loop.py`의 `LOOP_PROMPT`, `llm.py`의 `SYSTEM_PROMPT`(제한 모드·비교용 해석) |
| Tool/API 연동 | `agent/loop.py`(도구 15개 정의), `agent/services.py`, `agent/kakao.py` |
| 데이터 처리 코드 | `src/backend/jeju_lifepath/data/`, `scripts/build_barrier_free.py`, `scripts/fetch_*.py` |
| 사용 모델·라이브러리 | 아래 "AI 활용", `pyproject.toml`·`uv.lock`·`src/frontend/package.json` |
| 설치·실행 절차, 의존성, 환경변수 예시 | 아래 "설치와 실행", "환경변수", `.env.example` |
| 사용자 매뉴얼 | 아래 "사용자 매뉴얼" |
| 오류·제약사항 | 아래 "자주 생기는 문제", "제약사항" |
| 데이터 활용 명세(출처·이용조건·전처리 흐름) | 아래 "자료 준비", [docs/PROPOSAL.md](docs/PROPOSAL.md) 7장, [docs/DATA_AVAILABILITY.md](docs/DATA_AVAILABILITY.md) |
| 결과 설명자료(문제 정의·구조·기능·실행 결과·검증·한계·센터 활용) | [docs/PROPOSAL.md](docs/PROPOSAL.md), [docs/VALIDATION_REPORT.md](docs/VALIDATION_REPORT.md) |
| 평가용 LLM 키 | 제출 ZIP의 `.env`(LLM 키와 하루 요금 상한만. 평가 기간이 끝나면 폐기) |

## 주요 기능

| 기능 | 이렇게 물으면 |
|---|---|
| 근처 시설(화장실·약국·와이파이·파출소·공공심야약국·수유실·응급실) | "여기 근처 화장실 어디야?" |
| 걷는 길 세 경로(최단·균형·안전환경 우선), 휠체어·유모차 | "제주시청에서 동문시장까지 걸어서" |
| 가는 길에 들르기 | "공항에서 동문시장 가는 길에 약국" |
| 걷기 일정 만들기·고치기·저장 | "동문시장에서 빵집 두 곳이랑 카페 들르고 6시까지 돌아오는 코스" |
| 무장애 여행지·지식그래프 복합 조건 | "휠체어 타는 엄마랑 장애인 화장실 있는 해변 추천해줘" |
| 올레 코스, 지역 공백 | "올레 7코스 알려줘", "이도2동 약국 먼 곳" |
| 차·버스·택시 | 걷기 전용이라 카카오맵 길찾기로 넘기고, 걷는 길은 버튼으로 제안 |

## 실행 환경

- Windows 10/11 또는 Linux, CPU만(GPU·로컬 LLM 불필요). 센터 PC(i7-8700 / 32GB)에서 실행 가능
- Python 3.12 이상, Node.js 20 이상(화면 빌드에만)
- 인터넷: LLM API, 카카오 API, 생활안전지도 WMS

## 설치와 실행

```bash
# 1) 파이썬 패키지 (버전은 pyproject.toml·uv.lock에 고정)
python -m venv .venv
.venv/Scripts/activate              # Linux: source .venv/bin/activate
pip install -e ".[dev]"             # 평가까지: pip install -e ".[dev,eval]"

# 2) 환경변수
cp .env.example .env                # 아래 표의 값을 채운다

# 3) 화면 빌드
cd src/frontend && npm ci && npm run build && cd ../..

# 4) 자료 준비 (아래 "자료 준비" 표)

# 5) 서버 실행
uvicorn jeju_lifepath.api.app:app --port 8000
```

브라우저에서 `http://127.0.0.1:8000` → 회원가입 → 첫 화면이 AI 길잡이 대화다.

## 환경변수 (`.env`)

| 이름 | 쓰임 | 받는 곳 | 없으면 |
|---|---|---|---|
| `LLM_API_KEY` | Agent의 LLM(Meta Model API, muse-spark-1.3, OpenAI 호환) | 제출물의 평가용 키(사용 한도 설정, 평가 후 폐기) | 규칙 해석 제한 모드 |
| `LLM_DAILY_BUDGET_USD` | LLM 하루 요금 상한(달러, 실제 토큰으로 계산). 평가용은 5 | 제출물의 `.env`에 들어 있음 | 상한 없음 |
| `KAKAO_REST_API_KEY` | 장소·주소 검색(서버) | [카카오 디벨로퍼스](https://developers.kakao.com/) 앱 → REST API 키 | 장소 검색 기능 안내 문구로 끝남 |
| `KAKAO_JS_KEY` | 화면 지도 | 같은 앱의 JavaScript 키(플랫폼에 `http://127.0.0.1:8000` 등록) | 지도가 안 보임 |
| `SAFEMAP_API_KEY` | 범죄주의구간 지도 겹치기 | [생활안전지도 오픈API](https://www.safemap.go.kr/) | 겹쳐 보기만 꺼짐 |
| `DATA_GO_KR_API_KEY` | 사고 다발지역 자료 받기(스크립트) | [공공데이터포털](https://www.data.go.kr/) 일반 인증키 | 안전환경 경로의 사고 지표 없음 |
| `JEJU_DATAHUB_API_KEY`, `JEJU_HUB_*_DATA_ID` | 허브 와이파이 등 받기(스크립트) | [제주데이터허브](https://www.jejudatahub.net/) 마이페이지 → 프로젝트 관리 | 그 자료만 없음 |
| `LLM_BASE_URL`, `LLM_MODEL`, `AGENT_LLM=off` | 다른 OpenAI 호환 제공자로 바꾸기, LLM 끄기(선택) | — | `configs/agent_policy.json`의 값 |

실제 키는 저장소·제출물에 넣지 않는다(평가용 LLM 키만 대회 규칙에 따라 예외).

## 자료 준비

원본·가공 자료는 저장소에 없다(`data/private/`, `data/processed/`는 git 제외). 아래 순서로 받는다. 상세는 [docs/DATA_AVAILABILITY.md](docs/DATA_AVAILABILITY.md).

| 자료 | 출처(이용조건) | 받는 법 | 놓는 곳 | 없으면 |
|---|---|---|---|---|
| 제주 보행망 | OpenStreetMap(ODbL, 출처 표시) | `python scripts/check_osm_coverage.py --download` | `data/private/osm/jeju_highways.json` | 길찾기·일정·지역 공백 불가 |
| 허브 LINK 4종: 공공심야약국(1063)·모유수유시설(1165)·응급의료기관(1058)·올레코스(1236) | 제주데이터허브 → 공공데이터포털(제한 없음) | `python scripts/fetch_hub_links.py` (키 불필요) | `data/private/hub_link/` | 그 시설·올레 안내 없음 |
| 무장애여행정보: 허브 "무장애" 272건(CSV·한글 문서·압축) + 공공데이터포털 | 제주데이터허브(로그인 후 내려받기), 공공데이터포털 | 허브: 로그인 → "무장애" 검색 → 각 자료 첨부 받기(목록 `data/specs/barrier_free_hub_manifest.csv`) / 포털: `python scripts/fetch_barrier_free.py` → 묶기: `pip install -e ".[data]"` 후 `python scripts/build_barrier_free.py` | `data/private/barrier_free_hub/`, `barrier_free_hub_files/`, `barrier_free_zip/`, `barrier_free/` → `data/private/barrier_free.json` | 무장애 여행지·지식그래프·휠체어 확인 길 없음 |
| 공공와이파이(720)·사고다발지(493) | 제주데이터허브 | `python scripts/fetch_jeju_data.py` 후 `python -m jeju_lifepath.data.ingest` (스펙 `data/specs/`) | `data/processed/hub-*.geojson` | 와이파이 안내 없음 |
| CCTV(허브 510, 전국 CCTV 표준데이터) | 제주데이터허브, 공공데이터포털 | 각 사이트에서 CSV 내려받기 | `data/private/CCTV정보.csv`, `CCTV정보_제주특별자치도.csv` | 안전환경 경로의 CCTV 지표 없음 |
| 공중화장실 표준데이터 | 공공데이터포털 | CSV 내려받기 | `data/private/공중화장실정보_제주특별자치도.csv` | 근처 화장실 없음 |
| 교통사고 다발지역 | 한국도로교통공단·전국 표준데이터(공공데이터포털) | `python scripts/fetch_accident_zones.py` (`DATA_GO_KR_API_KEY`) | `data/processed/accident-zones.geojson` | 안전환경 경로의 사고 지표 없음 |
| 행정동 경계 | 통계청 SGIS, 가공 vuski/admdongkor(CC BY 4.0) | `python scripts/fetch_admdong.py` | `data/processed/jeju-admdong.geojson` | 지역 공백 불가 |
| 경찰관서 | 생활안전지도 | `python scripts/fetch_jeju_data.py` | `data/processed/safemap-police.geojson` | 근처 파출소 없음 |

키 연결 확인: `python scripts/check_api_keys.py`

## 사용자 매뉴얼

1. **회원가입·로그인:** 첫 화면에서 아이디(영문·숫자 3~20자)와 비밀번호를 만들어요.
2. **AI 길잡이(첫 화면):** 아래 입력창에 평소 말투로 물어요. 넓은 화면은 왼쪽 대화·오른쪽 지도, 폰은 위 지도·아래 대화예요.
   - 위치 사용을 허락하면 "근처", "여기서"가 지금 위치 기준이 돼요. 허락하지 않으면 지도 가운데를 기준으로 하거나 장소를 물어요.
   - 할망이 되물으면 버튼을 누르거나 말로 답해요.
   - 경로 카드를 누르면 그 경로가 지도에 그려져요. 장소 카드는 지도 위치로 이동해요.
3. **일정:** "동문시장에서 빵집 두 곳이랑 카페 들르고 6시까지 돌아오는 코스"처럼 말하면 초안이 나와요. "카페 빼줘"처럼 말로 고치고, **내 여행에 저장** 버튼을 눌러야 저장돼요.
4. **내 여행·플래너:** 위 메뉴의 "내 여행"에서 저장한 여행을 열고, 플래너에서 손으로 고치거나 AI 버튼으로 이어서 물어요.
5. **대화 관리:** 새 대화, 지난 대화 불러오기, 제목 바꾸기, 기록 지우기. 기록은 7일 뒤 지워져요.
6. **범죄주의구간:** 지도의 "범죄주의구간 보기" 버튼으로 생활안전지도 원본을 겹쳐 봐요(위험하다는 뜻이 아니에요).

## 자주 생기는 문제

| 증상 | 원인 | 해결 |
|---|---|---|
| "잠깐 생각이 막혔구나, 다시 말해 주렴" | LLM 응답 실패·시간 초과 | 다시 말하기. 계속되면 `LLM_API_KEY` 확인(`python scripts/check_api_keys.py`) |
| "오늘 AI 사용량이 다 차서…" | 하루 요금 상한(`LLM_DAILY_BUDGET_USD`) 도달 | 다음 날 자동 복구. 간단한 해석 모드로는 계속 동작 |
| 길이 안 잡히거나 시설을 못 찾음 | 자료 미준비(`data/private/`, `data/processed/`) | 위 "자료 준비" 표대로 받기 |
| 지도가 안 보임 | `KAKAO_JS_KEY` 없음 또는 카카오 앱 플랫폼에 주소 미등록 | 카카오 디벨로퍼스에서 `http://127.0.0.1:8000` 등록 |
| 화면이 안 열림(404) | 화면 미빌드 | `cd src/frontend && npm ci && npm run build` |

## 테스트와 평가

```bash
python -m pytest -q                                       # 자동 테스트(합성 세계, 키 불필요)
python evals/run_agent_evals.py                           # 도구 호출 평가(규칙 해석, 키 불필요)
python evals/run_agent_evals.py --llm real --mode loop    # 실 LLM 루프(사용료 발생)
python evals/run_agent_evals.py --llm real --mode parse   # 비교용: 한 번 해석 + 의도별 처리
python evals/run_quality_evals.py --llm real --judge codex  # 품질 평가(DeepEval·openevals, Codex 채점)
```

promptfoo 공격 평가는 `evals/promptfoo/README.md`.

## 제약사항

- 걷기 전용이다. 차·버스·택시 경로는 카카오맵 길찾기로 넘긴다(앱 안 차 경로·택시 요금 없음).
- 휠체어 통행이 확인된 길은 제주 보행 구간의 약 0.9%뿐이라, 휠체어 길은 확인 안 된 길 비율을 경고와 함께 안내한다.
- 무장애 자료는 2019~2021년 조사, 시설 영업 여부는 확인하지 못한다. 공사·통제 정보는 반영하지 않는다.
- 카카오 운영정책에 따라 카카오 결과(장소 이름·주소·좌표)는 저장하지 않고 외부 LLM에 보내지 않는다.
- LLM 루프 응답은 요청당 평균 약 10초(실측)다.
- 화면과 답은 한국어만 지원한다.

## 출처 표기

- 지도 데이터 © OpenStreetMap contributors (ODbL)
- 행정동 경계: 통계청 SGIS(공공누리 제1유형)를 vuski/admdongkor가 가공, CC BY 4.0
- 제주데이터허브, 공공데이터포털, 생활안전지도, 카카오맵·카카오 로컬 API

## AI 활용

- 서비스 안: Meta muse-spark-1.3(요청 해석·도구 선택). 거리·시간·경로 숫자는 만들지 않는다.
- 개발: Anthropic Claude Code(코드·문서 작성 보조), OpenAI Codex CLI(코드 검토·평가 채점), Google Gemini(설문대할망 캐릭터 그림). 결정과 확인은 참가자가 했다.
