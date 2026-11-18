"""여러 오픈소스 평가 도구로 한 번에 채점한다. 같은 실행 결과(합성 세계)를 네 가지로 본다.

    .venv/Scripts/python evals/run_quality_evals.py                     # 규칙 해석, 채점 LLM 없이
    .venv/Scripts/python evals/run_quality_evals.py --llm real --judge codex

1) agentevals(LangChain): 필요한 도구를 모두 불렀는가(도구 호출 경로 superset)
2) DeepEval ToolCorrectnessMetric: 기대한 도구와 부른 도구가 맞는가(LLM 없이 계산)
3) DeepEval GEval: 답이 요청을 해결했는가·지어낸 내용이 없는가·안전 안내(채점 LLM: codex)
4) openevals LLM-as-judge: 답의 도움됨과 근거(채점 LLM: codex)
프롬프트 공격 검사는 promptfoo(evals/promptfoo/)로 따로 돌린다.

채점 LLM으로 보내는 글은 합성 세계(가짜 카카오)의 답뿐이다(실제 카카오 결과는 외부 LLM 금지).
결과: evals/results/quality-<시각>.json (커밋하지 않음). 요약을 화면에 출력한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")  # 평가 내용을 DeepEval 서버로 보내지 않음
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src" / "backend"), str(ROOT), str(ROOT / "evals")]

from run_agent_evals import DATASET, EVALUATOR, as_messages  # noqa: E402
from tests.backend.fixtures.agent_world import CENTER, NOW, FakeKakao, make_context  # noqa: E402

from jeju_lifepath.agent.models import Policy, ToolError  # noqa: E402
from jeju_lifepath.agent.orchestrator import Agent  # noqa: E402
from jeju_lifepath.api import store as store_module  # noqa: E402
from jeju_lifepath.api.store import Store  # noqa: E402

RESULTS = ROOT / "evals" / "results"
CRITERIA = (
    "제주 걷기 여행 도우미(할망 말투)의 답을 평가한다. 답은 사용자가 화면에서 보는 그대로(문장·카드·지도 설명·안내) "
    "적혀 있고, [도구가 찾은 결과]는 도우미가 지도 검색·경로 계산 도구로 얻은 근거다. 장소·거리가 도구 결과와 맞으면 "
    "지어낸 것이 아니다. 일정의 순서 번호(1번, 2번)와 '(다녀옴)'·'(고정)' 표시는 정상 화면 표시다. "
    "아래를 모두 지키면 높은 점수다. "
    "(1) 사용자가 부탁한 일을 해결했거나, 할 수 없으면 왜 못 하는지와 다음에 할 말을 알려 줬다. "
    "(2) 도구 결과에 없는 장소·시간·사실을 지어내지 않았다. "
    "(3) 응급·안전·자료 한계 같은 필요한 안내를 빠뜨리지 않았다. "
    "(4) 영어 코드·내부 번호·JSON 같은 내부 값을 사용자에게 보이지 않았다."
)
OPENEVALS_PROMPT = (
    "너는 엄격한 채점자다. 아래 대화(앞 대화 포함)와 도우미의 마지막 답, 도구가 찾은 결과를 보고, "
    "답이 사용자 요청에 도움이 되고 근거 없는 내용을 지어내지 않았으면 true, 아니면 false로 채점하라.\n"
    + CRITERIA + "\n\n<대화>\n{inputs}\n</대화>\n<답>\n{outputs}\n</답>"
)


def view(resp: dict) -> str:
    """도우미 답을 사용자가 화면에서 보는 그대로 글로."""
    out = [f"할망: {resp['message']}"]
    for q in resp.get("questions") or []:
        opts = ", ".join(o["label"] for o in q.get("options") or [])
        out.append(f"질문: {q.get('text', '')}" + (f" (버튼: {opts})" if opts else ""))
    it = resp.get("itinerary") or {}
    for st in it.get("stops", []):
        mark = " (다녀옴)" if st.get("done") else " (고정)" if st.get("fixed") else ""
        walk = f" · 걸어서 약 {st['walk_minutes']}분" if st.get("walk_minutes") is not None else ""
        out.append(f"일정 {st['number']}번: {st['name']}{mark} — {st.get('detail', '')}{walk}")
    if it.get("back_label"):
        out.append(f"일정 마지막: {it['back_label']} 약 {it.get('back_minutes')}분, 모두 걸어서 약 {it.get('total_minutes')}분")
    for pl in resp.get("places") or []:
        out.append(f"카드: {pl['name']} — {pl.get('detail', '')}")
    routes = (resp.get("routes") or {}).get("routes") or []
    for r in routes:
        if r.get("found"):
            out.append(f"지도에 그린 경로: {r['title']} 약 {r.get('minutes')}분")
    if resp.get("track"):
        out.append("지도에 그린 선: 조사자가 걸은 무장애 코스")
    out += [f"안내: {m}" for m in [*resp.get("missing_data", []), *resp.get("warnings", [])]]
    return "\n".join(out)


def tool_facts(trajectory: list[dict]) -> str:
    """도구가 찾은 결과(채점 근거). 합성 세계의 결과라 외부로 보내도 된다."""
    keep = ("name", "distance_m", "notes", "address", "detail", "category", "hours")
    out = []
    for t in trajectory:
        r = t.get("result") or {}
        found = [json.dumps({k: x[k] for k in keep if x.get(k) not in (None, "", [])}, ensure_ascii=False)
                 for x in r.get("places", []) if isinstance(x, dict)][:8]
        for x in r.get("places", [])[:8]:  # 무장애 여행지의 조사 자료 요약
            if isinstance(x, dict) and "barrier_points" in x:
                doc = x.get("doc") or {}
                found.append(json.dumps({"장소": x["name"], "장애인화장실": doc.get("disabled_toilet"),
                                         "화장실지점": x.get("toilet_points"), "주의지점": x.get("barrier_points"),
                                         "조사": "2019~2021년 무장애여행정보"}, ensure_ascii=False))
        found += [json.dumps(c, ensure_ascii=False) for c in r.get("courses", [])][:5]
        if r.get("routes"):
            found += [f"{x.get('title')} 약 {x.get('minutes')}분" for x in r["routes"] if x.get("found")]
        if r.get("label"):
            found.append(f"위치 찾음: {r['label']}")
        out.append(f"- {t['name']}: " + (", ".join(map(str, found)) if found else "결과 없음 또는 계산 값"))
    return "\n".join(out) or "(도구를 부르지 않음)"


def run_case(case: dict, llm, tmp: Path) -> dict:
    """한 문제를 합성 세계에서 실행하고, 채점에 쓸 입력·답·부른 도구를 돌려준다."""
    store_module.SCRYPT_N = 2**10
    store = Store(tmp / f"{case['id']}.db")
    uid = store.create_user("evaluser", "password123")
    kakao = FakeKakao()
    for kind in case.get("inject", {}).get("kakao_fail", []):  # 장애 주입 시나리오(run_agent_evals와 같게)
        kakao.fail_queue.append(ToolError(kind, "injected"))
    agent = Agent(store, make_context(kakao=kakao), llm=llm, clock=lambda: NOW, sleep=lambda _s: None)
    agent.keep_tool_results = True  # 채점기에 도구가 찾은 근거를 보여 주려고(합성 세계라 카카오 자료 아님)
    conv, calls, resp, talk = None, [], None, []
    for turn in case["turns"]:
        loc = CENTER if turn.get("location") == "center" else None
        resp = agent.handle_message(uid, uuid.uuid4().hex, turn["text"], conversation_id=conv, location=loc)
        conv = resp["conversation_id"]
        calls += agent.last_trajectory
        last = [c["name"] for c in agent.last_trajectory]  # 채점하는 마지막 말에서 부른 도구
        talk.append((turn["text"], view(resp), tool_facts(agent.last_trajectory)))
    answer = {"답": resp["message"], "화면": talk[-1][1], "도구": talk[-1][2]}
    earlier = "\n\n".join(f"사용자: {u}\n{v}" for u, v, _ in talk[:-1])
    return {
        "id": case["id"],
        "turns": [t["text"] for t in case["turns"]],
        "answer": answer,
        "earlier": earlier,
        "tools": [c["name"] for c in calls],
        "last_tools": last,
        "expected_tools": case["expect"]["required_tools"],
        "interpreter": resp["interpreter"],
    }


def score_agentevals(r: dict) -> bool:
    ref = as_messages(r["turns"], [{"name": n, "args": {}} for n in r["expected_tools"]], "")
    out = as_messages(r["turns"], [{"name": n, "args": {}} for n in r["tools"]], r["answer"]["답"])
    return bool(EVALUATOR(outputs=out, reference_outputs=ref)["score"])


def judge_input(r: dict) -> str:
    head = f"[앞 대화]\n{r['earlier']}\n\n" if r["earlier"] else ""
    return head + f"[지금 사용자 말]\n{r['turns'][-1]}\n\n[도구가 찾은 결과]\n{r['answer']['도구']}"


def deepeval_case(r: dict):
    from deepeval.test_case import LLMTestCase, ToolCall  # noqa: PLC0415

    return LLMTestCase(
        input=judge_input(r),
        actual_output=r["answer"]["화면"],
        # 마지막 말에서 부른 도구(앞 말의 일정 만들기 등은 그 말의 몫)
        tools_called=[ToolCall(name=n) for n in dict.fromkeys(r["last_tools"])],
        expected_tools=[ToolCall(name=n) for n in r["expected_tools"]],
    )


def score_tool_correctness(r: dict) -> float:
    from deepeval.metrics import ToolCorrectnessMetric  # noqa: PLC0415

    from codex_judge import deepeval_model  # noqa: PLC0415

    # 도구 이름 비교라 LLM을 부르지 않는다. 기본(OpenAI) 모델이 키를 찾지 않게 codex 모델 객체를 넘김
    m = ToolCorrectnessMetric(threshold=1.0, model=deepeval_model())
    m.measure(deepeval_case(r))
    return float(m.score)


def score_geval(r: dict, judge) -> tuple[float, str]:
    from deepeval.metrics import GEval  # noqa: PLC0415
    from deepeval.test_case import LLMTestCaseParams  # noqa: PLC0415

    m = GEval(
        name="도우미 답 품질",
        criteria=CRITERIA,
        evaluation_steps=[
            "사용자 말에서 부탁한 일을 찾는다.",
            "답이 그 일을 해결했는지, 못 했으면 이유와 다음 행동을 알려 줬는지 본다.",
            "답의 장소·시간·사실이 부탁·안내와 어긋나거나 지어낸 것 같은지 본다.",
            "필요한 안전·한계 안내가 있는지, 내부 값(영어 코드·번호·JSON)이 보이지 않는지 본다.",
        ],
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
        model=judge,
        threshold=0.6,
        async_mode=False,
    )
    m.measure(deepeval_case(r))
    return float(m.score), m.reason or ""


def score_openevals(r: dict, client) -> tuple[bool, str]:
    from openevals.llm import create_llm_as_judge  # noqa: PLC0415

    judge = create_llm_as_judge(prompt=OPENEVALS_PROMPT, judge=client, model="codex", feedback_key="helpful")
    out = judge(inputs=judge_input(r), outputs=r["answer"]["화면"])
    return bool(out["score"]), out.get("comment") or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", choices=["none", "real"], default="none")
    ap.add_argument("--judge", choices=["none", "codex"], default="none")
    args = ap.parse_args()
    llm, llm_note = None, "규칙 해석"
    if args.llm == "real":
        from jeju_lifepath.api.app import default_llm  # noqa: PLC0415

        llm = default_llm(Policy.load())
        if llm is None:
            print("실 LLM 키가 없어 규칙 해석으로 돌림")
        else:
            llm_note = f"실 LLM ({llm.model})"
    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    client = gjudge = None
    if args.judge == "codex":
        from codex_judge import CodexClient, deepeval_model  # noqa: PLC0415

        client, gjudge = CodexClient(), deepeval_model()
    rows = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        for case in cases:
            t0 = time.monotonic()
            r = run_case(case, llm, Path(tmp))
            r["agentevals"] = score_agentevals(r)
            r["tool_correctness"] = score_tool_correctness(r)
            if client is not None:
                try:
                    r["geval"], r["geval_reason"] = score_geval(r, gjudge)
                    r["openevals"], r["openevals_reason"] = score_openevals(r, client)
                except Exception as e:  # noqa: BLE001 - 채점 실패는 그 칸만 비움(실행 결과는 남김)
                    r["judge_error"] = type(e).__name__
            r["seconds"] = round(time.monotonic() - t0, 1)
            rows.append(r)
            print(f"{r['id']:<36} agentevals={'O' if r['agentevals'] else 'X'} "
                  f"tool={r['tool_correctness']:.2f} "
                  + (f"geval={r.get('geval', float('nan')):.2f} openevals={'O' if r.get('openevals') else 'X'}"
                     if client else "채점LLM=없음"), flush=True)
    n = len(rows)
    summary = {
        "when": datetime.now(UTC).isoformat(timespec="seconds"),
        "interpreter": llm_note,
        "judge": "OpenAI Codex CLI" if client else None,
        "agentevals": f"{sum(r['agentevals'] for r in rows)}/{n}",
        "tool_correctness_mean": round(sum(r["tool_correctness"] for r in rows) / n, 3),
    }
    if client:
        judged = [r for r in rows if "geval" in r]
        summary["geval_mean"] = round(sum(r["geval"] for r in judged) / max(1, len(judged)), 3)
        summary["geval_pass"] = f"{sum(r['geval'] >= 0.6 for r in judged)}/{len(judged)}"  # noqa: PLR2004
        summary["openevals"] = f"{sum(bool(r.get('openevals')) for r in judged)}/{len(judged)}"
        summary["judge_errors"] = sum(1 for r in rows if "judge_error" in r)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"quality-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print(f"결과 파일: {out.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
