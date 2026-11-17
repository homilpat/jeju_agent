# Agent 구현 계획과 대응표 (REQUIREMENTS 12~14절)

갱신: 2026-11-18. 이 문서는 구현된 구조를 기록한다. 완료 판정은 `docs/VALIDATION_REPORT.md`를 따른다.

## 1. 구조

```
화면(Chat) -> POST /api/agent/messages -> Agent(orchestrator.py)
  1) 해석: parse.py(규칙) + llm.py(있으면 LLM, 스키마 검증·문장 근거 확인)
  2) 정보 보완: 부족하면 needs_input(질문 묶음), 대화 상태에 대기 요청 저장
  3) 도구 실행: tools.py(ToolRunner: 인자 검증·timeout·재시도·예산·취소·기록)
  4) 관찰: 0건이면 반경 확대, 계단 등으로 못 가는 후보는 빼고 다음 후보, 버전 충돌이면 최신 여행에 다시 적용
  5) 검증: itinerary.validate + app.Planner 규칙
  6) 게시: Store.commit_trip(기대 버전·취소·중복 게시를 한 트랜잭션에서 확인)
  7) 응답: present.py(사용자 문장만) + 공통 응답 필드(12.7)
```

- 단일 오케스트레이터. 프레임워크(LangChain/LangGraph) 없이 기존 Python 엔진·FastAPI·SQLite·React를 그대로 쓴다.
- 거리·시간·통행 제약·일정 유효성·권한은 코드가 판정한다. LLM은 의도·조건 해석만 한다.
- 도구 순서는 의도별로 코드가 고른다(아래 4절 결정 기록 참고).

## 2. 도구 대응표 (12.2의 정규 이름)

| 정규 이름 | 기존 함수 | 읽기/변경 | timeout(잠정) |
|---|---|---|---|
| parse_trip_request | `agent/parse.py: parse_rules`, `agent/llm.py: llm_parse` (도구로 노출하지 않는 로컬 단계) | 읽기 | LLM 20초 |
| search_places | `agent/kakao.py: KakaoLocal.keyword` | 읽기 | 10초 |
| resolve_location | `KakaoLocal.address` + `keyword` (모여 있는 결과는 한 장소, 멀리 떨어진 동명은 후보 질문) | 읽기 | 10초 |
| find_nearby_facilities | 약국 `KakaoLocal.category(PM9)` / 화장실 `api.app.toilets_in` + 주소 검색 / 와이파이·경찰 `api.app.nearest` | 읽기 | 10초 |
| compare_routes | `routing.walk.WalkRouter.plan` + `presentation.route.render_plan` | 읽기 | 30초 |
| get_travel_matrix | `WalkRouter.distances_from` (§11.4 한 번 탐색으로 여러 곳) | 읽기 | 30초 |
| plan_itinerary | `agent/itinerary.py: plan_greedy`, 수정은 `orchestrator._plan_modify` | 계산 | — |
| validate_itinerary | `agent/itinerary.py: validate` + `api.app.Planner` | 계산 | — |
| get_trip_state / apply_trip_patch | `api/store.py: get_trip`, `commit_trip` | 변경(원자적 버전 검사) | — |

제외(12.0): 인물 관계, 메뉴·가격·영업시간 수집. 일정 엔진의 운영시간 검증은 자료가 없어 늘 "확인하지 못함"으로 표시한다.

## 3. 상태 저장 (SQLite migration 1, `api/store.py`)

| 표 | 내용 |
|---|---|
| planners.version | 여행 버전. 수동 저장·Agent 게시마다 1 증가 |
| conversations | 소유자, 대화 상태(대기 요청·슬롯·고를 후보·선호 이동 조건·대화 기록), revision |
| agent_runs | (사용자, run_id=메시지 ID) 키, payload 해시, 단계, 체크포인트, 응답, 취소 요청, 게시 여부 |
| tool_invocations | 호출 ID, 도구, 시도 번호, 인자 해시, 좌표를 소수 둘째 자리로 줄인 인자, 상태, 오류 종류, 소요시간, 결과 개수 요약 |

- 카카오 이름·주소·좌표는 저장하지 않는다(응답 저장본·체크포인트·대화 기록에서 뺀다). 여행에는 카카오 장소 ID와 검색어(예: "애월 빵집")만 둔다.
- 예외: 지금 위치에서 시작한 일정은 고칠 때 같은 출발·복귀점을 쓰려고 사용자 자신의 출발 좌표를 소수 넷째 자리(약 10m)로 여행에 저장한다. 여행을 지우면 함께 지워진다.
- 기록 보유기간 7일(잠정), 서버 시작 때 지난 기록을 지운다.
- 연결 하나를 잠금으로 직렬화한다(단일 uvicorn 프로세스 전제).

## 4. 결정 기록

| 결정 | 이유 | 틀리면 |
|---|---|---|
| 화면 이동은 해석한 요청 종류 그대로(일정→플래너, 길찾기→경로 안내) | 사용자 결정(2026-11-18): 대화 화면 하나에서 LLM 판단대로 이동, 플래너 안 AI 칸은 두지 않음 | — |
| 도구 선택은 의도별 코드 정책, LLM은 해석만 | 마감·재현성. 12.2는 "LLM이 허용 도구 선택"을 말하지만, 잘못된 도구 호출 위험 없이 AgentEvals 경로 평가가 결정론적으로 가능 | 새 요청 유형마다 코드 추가 필요. LLM 도구 선택은 다음 단계 |
| LLM 없으면 규칙 해석(제한 모드) | AG-204 | 표현이 목록 밖이면 도움말로 떨어짐 |
| LLM 출력 근거 확인 | 사용자 문장에 없는 지명·테마는 버림. 시각·숫자·일정 수정 지시(몇 번을 어떻게)·미지원 항목 이름은 규칙 값만 | 문장에 없는 표현을 바르게 바꿔 말한 경우도 버려짐 |
| 재개 시 다시 해석 | 상대 시각·운영 정보 최신성(AG-504). 읽기 도구만 다시 부르므로 중복 변경 없음 | LLM 호출이 한 번 더 듦 |
| 버전 충돌 시 장소 목록이 그대로일 때만 같은 변경을 재적용 | 12.5 "재조정". 목록이 바뀌었으면 번호가 달라지므로 오류로 알림 | 사용자가 다시 말해야 함 |
| 재개는 실행 당시 여행·버전에 고정 | 그사이 여행이 바뀌었으면 게시하지 않음 | — |
| 휠체어 일정은 만들지 않음 | 기준 보행망에 통행 확인 구간이 없음(§4.3) | 접근성 자료가 생기면 바로 동작 |
| 공공시설이 섞인 일정은 Agent가 고치지 않음 | 번호·장소 손실 방지 | 화면에서 고쳐야 함 |
| 새 일정 만들기는 언제나 새 여행 | 기존 여행을 덮지 않기 | — |

## 5. 남은 일 (미완료)

- LLM이 도구를 직접 고르는 tool calling(현재 해석만).
- 실 사용자 위치·실 카카오 결과에 대한 반복 평가(지금은 수동 E2E 1회).
- 지역 시설 공백 분석(E01)은 대화로 연결하지 않음.
- 분류별 개수("빵집 2곳, 카페 1곳") 해석 없음: 총 개수를 분류 수로 나눠 올림.
- 수동 플래너 화면의 저장은 기대 버전을 보내지 않음(마지막 저장이 이김). Agent 게시만 버전 검사.
