"""LLM 제공자 어댑터(AG-203). 교체 가능: OpenAI 호환 chat/completions, 시험용 mock, 없음.

LLM은 요청 해석(의도·조건 추출)만 한다. 결과는 스키마 검증 후, 사용자 문장에 실제로 있는 말만
받아들인다(지명·테마를 지어내지 못하게). 시각·숫자는 규칙 파서 값만 쓴다.
설정: configs/agent_policy.json의 llm(base_url, model, timeout_s) — 환경변수 LLM_BASE_URL,
LLM_MODEL이 있으면 그 값을 쓴다. 키는 LLM_API_KEY(.env), 로그·응답·기록에 넣지 않는다.
2026-11-18 확인: Meta Model API(https://api.meta.ai/v1)의 /models, /chat/completions가
OpenAI 호환 형식으로 응답함(muse-spark-1.3, reasoning_effort=low에서 약 5초).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol

from pydantic import ValidationError

from jeju_lifepath.agent.models import ParseResult, ToolError
from jeju_lifepath.agent.parse import FACILITIES, THEMES

SYSTEM_PROMPT = """너는 제주 도보 여행 도우미의 요청 해석기다. 사용자 문장 하나를 읽고 JSON 하나만 출력한다.
설명·코드블록 없이 JSON만. 형식:
{"intent": "guide|route|plan_create|plan_modify|chat|unsupported",
 "location_text": 문자열|null, "use_current_location": true|false, "destination_text": 문자열|null,
 "round_trip": true|false, "facility": "pharmacy|toilet|wifi|police"|null, "place_query": 문자열|null,
 "mobility": "STANDARD|WHEELCHAIR|STROLLER"|null, "themes": [문자열],
 "theme_counts": {"문장 속 장소 종류": 들를 곳 수} (종류별 개수를 말했을 때만, 예: {"빵집": 2, "카페": 1}),
 "changes": [{"op": "exclude|keep|complete|prioritize|add_theme", "target": 정수|null, "theme": 문자열|null}],
 "unsupported_features": [문자열]}
규칙: guide=근처 시설·장소 설명 조회, route=두 장소 사이 걷는 길, plan_create=여러 곳을 들르는 일정 만들기,
plan_modify=이미 있는 일정 고치기, chat=인사·잡담, unsupported=차량·예약·결제처럼 제공하지 않는 일.
문장에 없는 지명·장소를 만들지 마라. 시각은 출력하지 마라. 모르면 null."""


class LLM(Protocol):
    mode: str  # "llm" | "mock"

    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatibleLLM:
    mode = "llm"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_s: float,
        reasoning_effort: str | None = None,
        opener: Callable = urllib.request.urlopen,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._key = api_key
        self.timeout_s = timeout_s
        self.reasoning_effort = reasoning_effort
        self._open = opener

    def complete(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                "User-Agent": "jeju-lifepath",
            },
        )
        try:
            with self._open(req, timeout=self.timeout_s) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            raise _classify_http(e.code, e.headers.get("Retry-After")) from None
        except TimeoutError:
            raise ToolError("timeout", "llm") from None
        except (urllib.error.URLError, OSError):
            raise ToolError("transient", "llm network") from None
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ToolError("invalid_response", "llm shape") from None


def _classify_http(code: int, retry_after: str | None) -> ToolError:
    if code in (401, 403):
        return ToolError("auth_config", f"http {code}")
    if code == 429 or code >= 500:
        wait = float(retry_after) if retry_after and retry_after.isdigit() else None
        return ToolError("transient", f"http {code}", wait)
    return ToolError("invalid_args", f"http {code}")


class MockLLM:
    """시험용. 미리 정한 응답을 돌려준다. 이 결과는 실 LLM 검증으로 보고하지 않는다(AG-204)."""

    mode = "mock"

    def __init__(self, reply: Callable[[str], str]):
        self._reply = reply

    def complete(self, system: str, user: str) -> str:
        return self._reply(user)


def _json_block(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no json")
    return json.loads(m.group(0))


def llm_parse(llm: LLM, text: str, rules: ParseResult) -> ParseResult:
    """LLM 해석을 규칙 해석 위에 겹친다. 검증에 실패하면 ValueError(호출 쪽이 규칙 결과를 씀)."""
    data = _json_block(llm.complete(SYSTEM_PROMPT, text))

    def grounded(value) -> str | None:  # 사용자 문장에 실제로 있는 말만
        return value if isinstance(value, str) and value and value in text else None

    slots = rules.slots.model_copy()
    sources = dict(rules.slot_sources)
    for name in ("location_text", "destination_text", "place_query"):
        v = grounded(data.get(name))
        if v and getattr(slots, name) is None:
            setattr(slots, name, v)
            sources[name] = "inferred"
    if data.get("use_current_location") is True and not slots.location_text:
        slots.use_current_location = True
    if data.get("round_trip") is True:
        slots.round_trip = True
    if data.get("facility") in set(FACILITIES.values()) and slots.facility is None:
        slots.facility = data["facility"]
        sources["facility"] = "inferred"
    if (
        data.get("mobility") in ("WHEELCHAIR", "STROLLER")
        and slots.mobility is None
        and ("휠체어" in text or "유모차" in text)
    ):
        slots.mobility = data["mobility"]
    themes = list(slots.themes)
    for th in data.get("themes") or []:
        key = THEMES.get(th) if isinstance(th, str) and th in text else None
        if key and key not in themes:
            themes.append(key)
    counts = {}
    raw_counts = data.get("theme_counts")
    for word, n in (raw_counts.items() if isinstance(raw_counts, dict) else []):
        key = THEMES.get(word) if isinstance(word, str) and word in text else None
        if key and isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 10:  # noqa: PLR2004
            counts[key] = counts.get(key, 0) + n
            if key not in themes:
                themes.append(key)
    slots.themes = themes
    if counts:
        slots.theme_counts = counts
        sources["theme_counts"] = "inferred"
    try:
        # 일정 수정 지시(무엇을, 몇 번)와 미지원 항목 이름은 규칙 해석 값만 쓴다:
        # LLM이 번호를 바꾸거나 영문 코드를 만들어도 실행·화면에 들어가지 않게
        result = ParseResult(
            intent=data.get("intent"),
            slots=slots,
            ambiguities=rules.ambiguities,
            requested_changes=rules.requested_changes,
            unsupported_features=rules.unsupported_features,
            slot_sources=sources,
            reference_time=rules.reference_time,
            parser=llm.mode,
        )
    except (ValidationError, TypeError) as e:
        raise ValueError("llm schema") from e
    return result
