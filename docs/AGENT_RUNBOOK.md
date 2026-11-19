# Agent 운영 안내 (AI 길잡이)

## 실행

```
.venv/Scripts/python -m pip install -e ".[dev,eval]"     # 또는 uv sync --extra dev --extra eval
cd src/frontend && npm run build && cd ../..
.venv/Scripts/uvicorn jeju_lifepath.api.app:app --port 8000
```

화면: 로그인하면 **AI 길잡이 대화 화면**이 첫 화면이다(넓은 화면: 왼쪽 대화·오른쪽 지도, 폰: 위 지도·아래 대화). 대화는 하나로 이어진다.
다른 화면(내 여행·플래너·여행 안내)의 오른쪽 아래 AI 버튼은 이 화면으로 온다. 플래너에서 누르면 손으로 고친 것을 먼저 저장하고 그 여행을 대화에 가져온다("고르신 여행을 함께 보고 있어요").
- 대화: 위쪽 "새 대화"로 새로 시작, "지난 대화"로 목록을 열어 불러오기·제목 바꾸기·제목 지우기·지우기, "기록 삭제"로 지금 대화 지우기. 기록은 7일 보유기간이 지나면 저절로 지워진다.
- 내 여행 카드의 "AI 길잡이에서 보기": 그 여행을 길잡이로 가져와 장소를 다시 찾고 고른 경로를 지도에 그린다. 이어 갈 대화가 있으면 "지금 대화에 이어서 / 새 대화로"를 고른다. 길찾기·장소 찾기 답이 오면 그 답의 지도를 보여 준다.
- 일정 만들기·고치기: AI가 만든 일정은 **초안**이다. 내 여행 목록에 보이지 않고, 대화 안의 "내 여행에 저장"을 눌러야 들어간다.
  저장된 여행을 대화에서 고치면 원래 여행은 그대로 두고 고친 초안을 따로 만든다. 저장할 때 "원래 여행에 덮어쓰기"와 "새 여행으로 저장" 중 고른다.
- 길찾기: 경로 유형(최단·균형·안전환경 우선)을 말하지 않았으면 먼저 묻는다(대화에서 한 번 정하면 다시 묻지 않음). 대화 안에 세 경로 카드가 나오고 고른 경로가 지도에 그려진다.
- 가는 길에 들를 곳: "공항에서 동문시장 가는 길에 약국" → 덜 돌아가는 순서로 최대 6곳(§11 규칙), 고르는 건 사용자.
- 지역 공백: "이도2동 약국 부족한 곳" → 그 읍면동 안 길에서 가장 가까운 시설까지 걷는 시간(절반·가장 먼 곳). 경계 자료는 `scripts/fetch_admdong.py`.
- 말로 저장: "저장해줘" → 초안을 내 여행에 저장(원래 여행이 있으면 덮어쓰기/새 여행을 물음).
- 그 밖: 대화에 남는다.
응답의 `screen`(planner/route/chat)은 지금 화면에서 쓰지 않는다. `unsaved`(초안이면 `{replaces}`, 저장된 여행이면 null)는 저장 버튼을 정하는 데만 쓰고 화면에 보이지 않는다.

## 설정 (`.env`, 값은 로그·응답에 넣지 않음)

| 이름 | 쓰임 | 없으면 |
|---|---|---|
| `KAKAO_REST_API_KEY` | 서버 쪽 장소·주소 검색 | 장소 검색 도구가 설정 오류로 끝남(걷는 길 비교는 동작) |
| (키 없음) `scripts/fetch_hub_links.py` | 허브 LINK 자료(심야약국·수유실·응급실·올레) 받기 | 그 기능만 결과 없음 |
| `LLM_API_KEY` | 요청 해석의 주인(Meta Model API, OpenAI 호환). 앞 대화·화면을 함께 봄 | 규칙 해석 제한 모드(낱말 목록) |
| `LLM_BASE_URL`, `LLM_MODEL` | 제공자 바꾸기(선택) | `configs/agent_policy.json`의 llm 값 |
| `AGENT_LLM=off` | LLM 끄기 | — |

예산·timeout·검색 반경 등은 `configs/agent_policy.json`(모두 잠정값).

## API

| 요청 | 설명 |
|---|---|
| `POST /api/planners/{id}/save?overwrite=` | AI 초안을 내 여행에 넣는다. overwrite=true면 고친 원래 여행에 덮어쓰고 초안은 지운다. 초안이 아니거나 남의 것이면 404 |
| `POST /api/agent/messages` | `{message_id, conversation_id?, text, location?, trip_id?, map_view?, screen?}`. screen은 지금 화면 `{name: planners|editor|travel|chat, travel_stop?}`, AI 해석에만 쓰고 저장 안 함. map_view는 지금 보이는 지도(가운데·네 모서리), 저장 안 함. 같은 message_id 재전송은 같은 응답, 다른 내용이면 409 |
| `GET /api/agent/runs/{run_id}` | 진행 상태. `?detail=true`면 도구 호출 기록(본인 것만) |
| `POST /api/agent/runs/{run_id}/cancel` | 취소 요청. 게시 직전에 확인해 일정에 반영하지 않음 |
| `POST /api/agent/runs/{run_id}/resume` | 서버가 꺼졌다 켜져 중단된 실행 이어가기. `{location?}` |
| `GET /api/agent/conversations/{id}` | 새로고침 후 대화 복원(대화 기록 문장, 연결된 여행) |
| `GET /api/agent/conversations` | 지난 대화 목록(최근 것부터, 첫 질문 앞 40자가 제목) |
| `PATCH /api/agent/conversations/{id}` | 대화 제목 바꾸기 `{title}`. 비우거나 null이면 기본 제목(첫 질문)으로 |
| `DELETE /api/agent/conversations/{id}` | 대화 기록 지우기(그 대화의 실행·도구 호출 기록도 함께) |

run_id는 화면이 만든 message_id와 같다.

## 제약사항

- **외국어(영어) 안내는 추후 과제다.** 사용자에게 보이는 문장(답 문장, 화면 글자)이 한국어로 코드에 들어 있고, LLM 답도 한국어 할망 말투로 정해 두었다. 장소 이름은 카카오·허브 자료라 한국어이며, 카카오 운영정책상 장소 이름을 LLM으로 번역할 수 없다. 하려면 답 문장과 화면 글자를 언어별 문구표로 빼고, LLM 답을 사용자 언어로 쓰게 해야 한다. 내부 값 거르기는 영어 문장을 막지 않게 이미 맞춰 두었다(소문자 칸 이름은 한국어 문장에 섞였을 때만 막음).
- **휠체어 길 안내는 추후 개발 예정이다.** 지금은 통행이 확인된 길만 쓰는 규칙(요구사항 §4.3)인데, 제주 OSM 길 37,955개 중 휠체어 통행 정보가 있는 길은 252개(0.7%)뿐이라 대부분 "확인된 길이 없어 안내할 수 없다"고 답한다. 계단은 피할 수 있다(유모차는 계단을 피한 길로 안내).
- 공공심야약국·수유실·응급실·올레 시작점은 주소·지점 이름을 카카오 검색으로 그때그때 위치로 바꾼다. 이름이 비슷한 다른 곳이 먼저 나오면 위치가 틀릴 수 있다.

## 장애 대응

- 서버가 실행 도중 꺼지면 다음 시작 때 그 실행은 "중단", 끝나지 않은 도구 호출은 "불확실"로 표시된다. resume을 부르면 다시 해석하고 읽기 도구만 다시 부른 뒤, 아직 게시하지 않았을 때만 게시한다.
- 카카오·LLM 429/5xx/시간 초과는 최대 2번 다시 시도(Retry-After를 따름). 인증 오류는 다시 시도하지 않는다.
- 같은 실패가 3번 넘게 반복되거나 41초를 넘으면 멈추고, 일정은 바꾸지 않고 부분 결과만 알린다.

## 평가

```
.venv/Scripts/python evals/run_agent_evals.py              # 키 없이(규칙 해석), 합성 세계
.venv/Scripts/python evals/run_agent_evals.py --llm real   # 실 LLM 해석(사용료 발생, opt-in)
.venv/Scripts/python -m pytest -q                          # 결정론적 인수시험 포함 전체 테스트
```

여러 오픈소스 평가 도구로 같은 실행 결과를 채점한다(합성 세계, 가짜 카카오).

| 도구 | 보는 것 | 채점 | 실행 |
|---|---|---|---|
| agentevals (LangChain) | 필요한 도구를 모두 불렀나(도구 호출 경로) | 규칙 | 위 `run_agent_evals.py`, 아래 `run_quality_evals.py` |
| DeepEval ToolCorrectness | 마지막 말에서 기대한 도구를 불렀나 | 규칙 | `run_quality_evals.py` |
| DeepEval GEval | 요청 해결·지어내기 없음·안전 안내·내부 값 없음 | 채점 LLM: OpenAI Codex CLI | `run_quality_evals.py --judge codex` |
| openevals LLM-as-judge | 도움됨·근거 | 채점 LLM: OpenAI Codex CLI | `run_quality_evals.py --judge codex` |
| promptfoo | 프롬프트 공격 9개(지시 무시·남의 정보·키·운전·지어내기·응급) | 규칙(javascript) | `evals/promptfoo/README.md` |

```
.venv/Scripts/python evals/run_quality_evals.py --llm real --judge codex   # codex 로그인 필요, 약 15분
```

- 채점 LLM을 에이전트(muse-spark)와 다른 모델(Codex)로 둬 자기 답을 자기가 채점하지 않게 했다. codex가 없으면 `--judge none`으로 규칙 채점만 돈다.
- 채점 LLM에 보내는 글은 합성 세계의 답뿐이다. 실제 카카오 결과는 외부 LLM에 보내지 않는다(카카오 운영정책).

결과 파일은 `evals/results/`(커밋하지 않음). 요약은 `docs/VALIDATION_REPORT.md`.
