"""Agent 평가: AgentEvals(도구 호출 경로) + 자체 하드 제약 검사. 로컬에서 키 없이 돌아간다.

    .venv/Scripts/python evals/run_agent_evals.py                # 합성 세계, 규칙 해석
    .venv/Scripts/python evals/run_agent_evals.py --llm real     # 실 LLM 해석(opt-in, 사용료 발생)

결과: evals/results/<시각>.json (커밋하지 않음, 개인정보 없음). 요약을 화면에 출력한다.
AgentEvals 판정(trajectory superset: 필수 도구를 모두 불렀는가)과 자체 검사(금지 도구, 상태,
여행 변경 횟수, 금지 장소 등)를 따로 집계한다. 실행하지 못한 항목은 NOT_RUN으로 분모에서 뺀다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src" / "backend"), str(ROOT)]

from agentevals.trajectory.match import create_trajectory_match_evaluator  # noqa: E402
from tests.backend.fixtures.agent_world import CENTER, NOW, FakeKakao, make_context  # noqa: E402

from jeju_lifepath.agent.models import Policy, ToolError  # noqa: E402
from jeju_lifepath.agent.orchestrator import Agent  # noqa: E402
from jeju_lifepath.api import store as store_module  # noqa: E402
from jeju_lifepath.api.store import Store  # noqa: E402

DATASET = ROOT / "evals" / "datasets" / "agent_cases.json"
RESULTS = ROOT / "evals" / "results"
EVALUATOR = create_trajectory_match_evaluator(
    trajectory_match_mode="superset", tool_args_match_mode="ignore"
)


def as_messages(turns: list[str], calls: list[dict], final: str) -> list[dict]:
    """AgentEvals가 읽는 OpenAI 메시지 형식으로 실제 실행 trace를 옮긴다."""
    msgs = [{"role": "user", "content": t} for t in turns]
    if calls:
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c["args"], ensure_ascii=False),
                        }
                    }
                    for c in calls
                ],
            }
        )
    msgs.append({"role": "assistant", "content": final})
    return msgs


def run_case(case: dict, llm, tmp: Path, policy: Policy | None = None) -> dict:
    store_module.SCRYPT_N = 2**10
    store = Store(tmp / f"{case['id']}.db")
    uid = store.create_user("evaluser", "password123")
    kakao = FakeKakao()
    for kind in case.get("inject", {}).get("kakao_fail", []):
        kakao.fail_queue.append(ToolError(kind, "injected"))
    agent = Agent(
        store, make_context(policy, kakao=kakao), llm=llm, clock=lambda: NOW, sleep=lambda _s: None
    )
    conv, calls, statuses, versions, resp = None, [], [], [], None
    turn_seconds, turn_calls, first_stops = [], [], None
    started = time.monotonic()
    for turn in case["turns"]:
        loc = CENTER if turn.get("location") == "center" else None
        t0 = time.monotonic()
        resp = agent.handle_message(
            uid, uuid.uuid4().hex, turn["text"], conversation_id=conv, location=loc
        )
        turn_seconds.append(round(time.monotonic() - t0, 3))
        turn_calls.append(len(agent.last_trajectory))
        if resp.get("itinerary") and first_stops is None:
            first_stops = len(resp["itinerary"]["stops"])
        conv = resp["conversation_id"]
        calls += agent.last_trajectory
        statuses.append(resp["status"])
        # 여행이 바뀐 횟수: 저장 전 초안까지 센다(AI 일정은 저장 버튼 전까지 목록에 안 보이는 초안)
        versions.append(sum(v for (v,) in store._rows(
            "SELECT version FROM planners WHERE user_id = ?", (uid,))))
    elapsed = time.monotonic() - started
    trace = agent.run_status(uid, resp["run_id"], detail=True)["trace"]
    exp = case["expect"]
    last_turn_calls = agent.last_trajectory
    reference = as_messages(
        [t["text"] for t in case["turns"]],
        [{"name": n, "args": {}} for n in exp["required_tools"]],
        "",
    )
    output = as_messages(
        [t["text"] for t in case["turns"]],
        last_turn_calls if exp["intent"] == "plan_modify" else calls,
        resp["message"],
    )
    agentevals = EVALUATOR(outputs=output, reference_outputs=reference)
    names = {c["name"] for c in calls}
    hard = {
        "status": resp["status"] == exp["status"],
        "trip_changes": versions[-1] == exp["trip_changes"],
    }
    forbidden = exp.get("forbidden_tools", [])
    if forbidden == ["*"]:
        hard["forbidden_tools"] = not last_turn_calls
    elif forbidden:
        hard["forbidden_tools"] = not (names & set(forbidden))
    if "first_status" in exp:
        hard["first_status"] = statuses[0] == exp["first_status"]
    if "stops" in exp:
        hard["stops"] = bool(resp["itinerary"]) and len(resp["itinerary"]["stops"]) == exp["stops"]
    if "stops_delta" in exp:
        hard["stops_delta"] = (
            bool(resp["itinerary"]) and first_stops is not None
            and len(resp["itinerary"]["stops"]) == first_stops + exp["stops_delta"]
        )
    if "forbidden_stop" in exp:
        hard["forbidden_stop"] = bool(resp["itinerary"]) and exp["forbidden_stop"] not in [
            s["name"] for s in resp["itinerary"]["stops"]
        ]
    if exp.get("first_stop_done"):
        hard["first_stop_done"] = resp["itinerary"]["stops"][0]["done"]
    if exp.get("first_stop_fixed"):
        hard["first_stop_fixed"] = resp["itinerary"]["stops"][0]["fixed"]
    if "unverified_claim" in exp:
        hard["unverified_claim"] = any(
            exp["unverified_claim"] in c["text"] and not c["verified"] for c in resp["claims"]
        )
    if "missing_contains" in exp:  # 꼭 들어가야 하는 안내(예: 응급실 답의 119)
        hard["missing_contains"] = any(exp["missing_contains"] in m for m in resp.get("missing_data", []))
    if "answer_contains" in exp:  # 답(문장·카드·일정)에 꼭 나와야 하는 장소(복합 조건 해결)
        shown = [resp["message"], *(p["name"] for p in resp.get("places") or []),
                 *(s["name"] for s in (resp.get("itinerary") or {}).get("stops", []))]
        hard["answer_contains"] = any(exp["answer_contains"] in x for x in shown)
    if "min_attempts" in exp:
        hard["min_attempts"] = len(trace) >= exp["min_attempts"]
    return {
        "id": case["id"],
        "split": case["split"],
        "agentevals_superset": bool(agentevals["score"]),
        "hard": hard,
        "hard_pass": all(hard.values()),
        "tool_calls": len(calls),
        "turn_seconds": turn_seconds,  # 요청 하나당 걸린 시간(실행 제한 실측용)
        "turn_tool_calls": turn_calls,
        "seconds": round(elapsed, 3),
        "interpreter": resp["interpreter"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", choices=["none", "real"], default="none")
    ap.add_argument("--repeat", type=int, default=1, help="실 LLM 비결정성 확인용 반복 횟수")
    ap.add_argument("--mode", choices=["loop", "parse"], default="loop",
                    help="loop: LLM이 도구를 고르는 루프 / parse: 한 번 해석 + 의도별 처리(기존)")
    args = ap.parse_args()
    llm, llm_note = None, "NOT_RUN (규칙 해석만)"
    if args.llm == "real":
        from jeju_lifepath.api.app import default_llm  # noqa: PLC0415

        llm = default_llm(Policy.load())
        if llm is None:
            print("실 LLM 키가 없어 NOT_RUN")
            return 2
        llm_note = f"real ({llm.model})"
    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    rows = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # 윈도우: 열린 DB 파일
        for rep in range(args.repeat):
            for case in cases:
                row = run_case(case, llm, Path(tmp) / f"r{rep}", Policy.load(agent_loop=args.mode == "loop"))
                row["repeat"] = rep
                rows.append(row)
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    summary = {
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_commit": commit,
        "policy_version": Policy.load().version,
        "dataset": "evals/datasets/agent_cases.json",
        "world": "synthetic-grid (tests/backend/fixtures/agent_world.py)",
        "llm": llm_note,
        "mode": args.mode if llm is not None and hasattr(llm, "chat") else "parse",
        "mean_turn_seconds": round(sum(sum(r["turn_seconds"]) / len(r["turn_seconds"]) for r in rows) / len(rows), 2),
        "mean_tool_calls": round(sum(r["tool_calls"] for r in rows) / len(rows), 2),
        "real_kakao": "NOT_RUN",
        "cases": len(rows),
        "agentevals_superset_pass": sum(r["agentevals_superset"] for r in rows),
        "hard_pass": sum(r["hard_pass"] for r in rows),
        "by_split": {
            s: {
                "cases": sum(r["split"] == s for r in rows),
                "hard_pass": sum(r["hard_pass"] for r in rows if r["split"] == s),
            }
            for s in ("dev", "holdout")
        },
        "rows": rows,
    }
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in rows:
        bad = [k for k, v in r["hard"].items() if not v]
        print(
            f"{r['id']:<36} agentevals={'O' if r['agentevals_superset'] else 'X'} "
            f"hard={'O' if r['hard_pass'] else 'X ' + ','.join(bad)} calls={r['tool_calls']}"
        )
    print(
        f"AgentEvals {summary['agentevals_superset_pass']}/{len(rows)}, "
        f"hard {summary['hard_pass']}/{len(rows)}, LLM {llm_note}"
    )
    print(f"결과 파일: {out.relative_to(ROOT).as_posix()}")
    return 0 if summary["hard_pass"] == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
