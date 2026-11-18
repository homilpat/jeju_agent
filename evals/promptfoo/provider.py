"""promptfoo 파이썬 제공자: 합성 세계(가짜 카카오)에서 에이전트 한 번 실행.

출력은 JSON 글자 {"message", "status", "tools", "places", "notes"}. LLM_API_KEY가 있으면 실 LLM, 없으면 규칙 해석.
promptfoo가 부를 파이썬: 환경변수 PROMPTFOO_PYTHON을 .venv 파이썬으로(evals/promptfoo/README.md).
"""

import json
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src" / "backend"), str(ROOT)]

from tests.backend.fixtures.agent_world import CENTER, NOW, FakeKakao, make_context  # noqa: E402

from jeju_lifepath.agent.models import Policy  # noqa: E402
from jeju_lifepath.agent.orchestrator import Agent  # noqa: E402
from jeju_lifepath.api import store as store_module  # noqa: E402
from jeju_lifepath.api.app import default_llm  # noqa: E402
from jeju_lifepath.api.store import Store  # noqa: E402

LLM = default_llm(Policy.load())


def call_api(prompt, options, context):
    store_module.SCRYPT_N = 2**10
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = Store(Path(tmp) / "pf.db")
        uid = store.create_user("redteam", "password123")
        agent = Agent(store, make_context(kakao=FakeKakao()), llm=LLM, clock=lambda: NOW, sleep=lambda _s: None)
        r = agent.handle_message(uid, uuid.uuid4().hex, prompt, location=CENTER)
        out = {
            "message": r["message"],
            "status": r["status"],
            "tools": [c["name"] for c in agent.last_trajectory],
            "places": [p["name"] for p in r.get("places") or []],
            "notes": [*r.get("missing_data", []), *r.get("warnings", [])],
            "trip_changed": bool(store.list_planners(uid)) or bool(r.get("trip_id")),
        }
        store._db.close()
    return {"output": json.dumps(out, ensure_ascii=False)}
