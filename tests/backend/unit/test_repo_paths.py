"""Committed files must use repo-relative paths only (no personal absolute paths)."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# Windows drive + Users, git-bash /c/Users, macOS /Users, Linux /home.
_ABS = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]|/[a-z]/Users/|(?<![\w.])/Users/\w|/home/\w")
_BINARY = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".lock", ".webp", ".pyc"}


def test_no_absolute_personal_paths_in_tracked_files():
    if (ROOT / ".git").exists():
        files = subprocess.run(
            ["git", "ls-files", "-c"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    else:  # 제출 ZIP처럼 git 기록 없이 받은 사본: 내려받은 파일 전체를 검사
        skip = {".venv", "node_modules", "dist", "data", "__pycache__", ".pytest_cache"}
        files = [str(f.relative_to(ROOT)) for f in ROOT.rglob("*") if not skip & set(f.relative_to(ROOT).parts)]
    hits = []
    for name in files:
        path = ROOT / name
        if path.suffix in _BINARY or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{name}: {m.group(0)}" for m in _ABS.finditer(text)]
    assert not hits, hits


def test_submission_eval_env_allows_only_llm_keys():
    import importlib.util  # noqa: PLC0415

    import pytest  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("make_submission", "scripts/make_submission.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "LLM_API_KEY=abc" in mod.eval_env_text("LLM_API_KEY=abc\nKAKAO_REST_API_KEY=\n# note")
    with pytest.raises(ValueError):
        mod.eval_env_text("LLM_API_KEY=abc\nKAKAO_REST_API_KEY=secret")
    with pytest.raises(ValueError):
        mod.eval_env_text("KAKAO_JS_KEY=")
