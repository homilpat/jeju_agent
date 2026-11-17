# Agent 운영 안내 (AI 길잡이)

## 실행

```
.venv/Scripts/python -m pip install -e ".[dev,eval]"     # 또는 uv sync --extra dev --extra eval
cd src/frontend && npm run build && cd ../..
.venv/Scripts/uvicorn jeju_lifepath.api.app:app --port 8000
```

화면: 로그인 → "AI에게 일정 부탁하기" 또는 왼쪽 메뉴 "AI 길잡이".

## 설정 (`.env`, 값은 로그·응답에 넣지 않음)

| 이름 | 쓰임 | 없으면 |
|---|---|---|
| `KAKAO_REST_API_KEY` | 서버 쪽 장소·주소 검색 | 장소 검색 도구가 설정 오류로 끝남(걷는 길 비교는 동작) |
| `LLM_API_KEY` | 요청 해석(Meta Model API, OpenAI 호환) | 규칙 해석 제한 모드 |
| `LLM_BASE_URL`, `LLM_MODEL` | 제공자 바꾸기(선택) | `configs/agent_policy.json`의 llm 값 |
| `AGENT_LLM=off` | LLM 끄기 | — |

예산·timeout·검색 반경 등은 `configs/agent_policy.json`(모두 잠정값).

## API

| 요청 | 설명 |
|---|---|
| `POST /api/agent/messages` | `{message_id, conversation_id?, text, location?, trip_id?}`. 같은 message_id 재전송은 같은 응답, 다른 내용이면 409 |
| `GET /api/agent/runs/{run_id}` | 진행 상태. `?detail=true`면 도구 호출 기록(본인 것만) |
| `POST /api/agent/runs/{run_id}/cancel` | 취소 요청. 게시 직전에 확인해 일정에 반영하지 않음 |
| `POST /api/agent/runs/{run_id}/resume` | 서버가 꺼졌다 켜져 중단된 실행 이어가기. `{location?}` |
| `GET /api/agent/conversations/{id}` | 새로고침 후 대화 복원(최근 20턴 문장, 연결된 여행) |

run_id는 화면이 만든 message_id와 같다.

## 장애 대응

- 서버가 실행 도중 꺼지면 다음 시작 때 그 실행은 "중단", 끝나지 않은 도구 호출은 "불확실"로 표시된다. resume을 부르면 다시 해석하고 읽기 도구만 다시 부른 뒤, 아직 게시하지 않았을 때만 게시한다.
- 카카오·LLM 429/5xx/시간 초과는 최대 2번 다시 시도(Retry-After를 따름). 인증 오류는 다시 시도하지 않는다.
- 같은 실패가 3번 넘게 반복되거나 60초를 넘으면 멈추고, 일정은 바꾸지 않고 부분 결과만 알린다.

## 평가

```
.venv/Scripts/python evals/run_agent_evals.py              # 키 없이(규칙 해석), 합성 세계
.venv/Scripts/python evals/run_agent_evals.py --llm real   # 실 LLM 해석(사용료 발생, opt-in)
.venv/Scripts/python -m pytest -q                          # 결정론적 인수시험 포함 전체 테스트
```

결과 파일은 `evals/results/`(커밋하지 않음). 요약은 `docs/VALIDATION_REPORT.md`.
