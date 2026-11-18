"""채점용 LLM으로 OpenAI Codex CLI(codex exec)를 쓴다. 에이전트(muse-spark)와 다른 회사·다른 모델이라
자기 답을 자기가 채점하지 않는다. openevals에는 OpenAI 클라이언트 모양(CodexClient)으로,
DeepEval에는 사용자 정의 모델(CodexDeepEvalModel)로 끼운다.

주의: 채점하러 보내는 글은 합성 세계(tests/backend/fixtures/agent_world.py)의 답뿐이다.
실제 카카오 결과는 외부 LLM에 보내면 안 된다(카카오 운영정책, docs/development/open-decisions.md).
codex가 없으면 CodexUnavailable을 내고, 평가 스크립트는 채점 칸만 건너뛴다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

TIMEOUT_S = 300  # shortcut: 근거 없는 상한(codex 한 번에 보통 20~60초), 느리면 늘리기


class CodexUnavailable(RuntimeError):
    pass


def codex_complete(prompt: str) -> str:
    """codex에 한 번 묻고 마지막 답 글자를 돌려준다(읽기 전용, 빈 작업 폴더)."""
    exe = shutil.which("codex")
    if exe is None:
        raise CodexUnavailable("codex 명령이 없음")
    with tempfile.TemporaryDirectory() as work:
        out = Path(work) / "answer.txt"
        subprocess.run(
            [exe, "exec", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check",
             "-C", work, "-o", str(out), "-"],
            input=prompt, text=True, encoding="utf-8", capture_output=True, timeout=TIMEOUT_S, check=False,
        )
        if not out.exists():
            raise CodexUnavailable("codex가 답을 남기지 않음(로그인·네트워크 확인)")
        return out.read_text(encoding="utf-8")


def json_part(text: str) -> str:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("채점 답에 JSON이 없음")
    return m.group(0)


def _ask_json(prompt: str, schema: dict) -> str:
    rule = ("\n\n답은 아래 JSON 스키마에 맞는 JSON 하나만 출력하라. 설명·코드블록 없이.\n"
            + json.dumps(schema, ensure_ascii=False))
    return json_part(codex_complete(prompt + rule))


class _Completions:
    def create(self, *, messages, model=None, response_format=None, **_kw):
        prompt = "\n\n".join(
            m["content"] if isinstance(m["content"], str) else json.dumps(m["content"], ensure_ascii=False)
            for m in messages
        )
        schema = (response_format or {}).get("json_schema", {}).get("schema", {})
        content = _ask_json(prompt, schema)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class CodexClient:
    """openevals.create_llm_as_judge(judge=CodexClient(), model="codex")에 넘기는 OpenAI 클라이언트 모양."""

    chat = SimpleNamespace(completions=_Completions())


def deepeval_model():
    """DeepEval 사용자 정의 모델(DeepEvalBaseLLM). deepeval을 쓸 때만 불러온다."""
    from deepeval.models import DeepEvalBaseLLM  # noqa: PLC0415

    class CodexDeepEvalModel(DeepEvalBaseLLM):
        def __init__(self):
            super().__init__(model="codex")

        def load_model(self):
            return self

        def generate(self, prompt: str, schema=None):
            if schema is None:
                return codex_complete(prompt)
            return schema.model_validate_json(_ask_json(prompt, schema.model_json_schema()))

        async def a_generate(self, prompt: str, schema=None):
            return self.generate(prompt, schema)

        def get_model_name(self) -> str:
            return "OpenAI Codex CLI"

    return CodexDeepEvalModel()
