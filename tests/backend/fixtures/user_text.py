"""Leak check for user-facing text: JSON symbols, underscores (field names),
edge ids like ``A-B``, and upper-case codes (except allowed words)."""

import re

_LEAK = re.compile(r"[{}\[\]_]|\b[A-Z]-[A-Z]\b|\b[A-Z]{2,}\b|synthetic:")
_ALLOWED_CAPS = {"CCTV"}


def assert_user_text(text: str) -> None:
    bad = [m for m in _LEAK.findall(text) if m not in _ALLOWED_CAPS]
    assert not bad, f"내부값 노출 {bad}: {text}"
