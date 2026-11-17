"""Agent 내부 결과 모델. 사용자 문구는 presenter(present.py)가 따로 만든다."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jeju_lifepath.schemas import MobilityProfile, RoutePreference

ROOT = Path(__file__).resolve().parents[4]
POLICY_FILE = ROOT / "configs" / "agent_policy.json"

Intent = Literal["guide", "route", "plan_create", "plan_modify", "chat", "unsupported"]
# 응답 상태(12.7). 화면 코드가 분기에만 쓰고 사용자에게 보여주지 않는다
Status = Literal["ok", "needs_input", "partial", "infeasible", "unsupported", "error", "cancelled"]
Facility = Literal["pharmacy", "toilet", "wifi", "police"]
SlotSource = Literal["explicit", "previous", "default", "inferred"]


class Slots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_text: str | None = None  # 출발·기준 위치로 사용자가 말한 지명
    use_current_location: bool = False  # "여기", "근처" 등
    destination_text: str | None = None
    round_trip: bool = False  # "돌아오게"
    facility: Facility | None = None
    place_query: str | None = None  # "이 장소 설명해줘"의 장소 이름
    mobility: MobilityProfile | None = None
    transport: Literal["walk", "car", "bus", "taxi"] | None = None
    themes: list[str] = Field(default_factory=list)
    date: str | None = None  # YYYY-MM-DD (Asia/Seoul)
    depart_at: str | None = None  # ISO datetime, Asia/Seoul
    return_by: str | None = None
    stop_count: int | None = Field(default=None, ge=1, le=10)
    # 분류별 들를 곳 수 {"빵집": 2, "카페": 1}. LLM이 판단하고 llm_parse가 검증한다
    theme_counts: dict[str, int] = Field(default_factory=dict)
    stay_minutes: int | None = Field(default=None, ge=0, le=600)
    free_minutes: int | None = Field(default=None, ge=1, le=1440)
    route_preference: RoutePreference | None = None
    prefer_cctv: bool | None = None


class Change(BaseModel):
    """일정 수정 지시 한 개. target은 일정의 몇 번째 장소(1부터)."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["exclude", "keep", "complete", "prioritize", "add_theme"]
    target: int | None = Field(default=None, ge=1, le=30)
    last: bool = False  # "마지막 장소"
    theme: str | None = None


class ParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    slots: Slots = Field(default_factory=Slots)
    missing_slots: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    requested_changes: list[Change] = Field(default_factory=list)
    unsupported_features: list[str] = Field(default_factory=list)
    slot_sources: dict[str, SlotSource] = Field(default_factory=dict)
    reference_time: str = ""  # 상대 시각("오늘", "6시")을 푼 기준 시각
    parser: Literal["rules", "llm", "mock"] = "rules"


class ToolError(Exception):
    """도구 실패. kind로 다음 행동을 정한다(12.5 표).
    transient: 제한적 재시도 / invalid_args: 재시도 안 함 / auth_config: 설정 안내, 재시도 안 함
    unsupported / not_found: 정상적인 실패로 관찰 / timeout: 일시 장애와 같게 취급."""

    def __init__(self, kind: str, detail: str = "", retry_after: float | None = None):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.retry_after = retry_after


@dataclass
class Policy:
    version: str
    deadline_s: float
    max_tool_calls: int
    max_llm_calls: int
    max_replans: int
    max_same_failure: int
    max_retries: int
    retry_backoff_s: float
    tool_timeout_s: dict
    max_candidates: int
    search_radius_m: int
    widened_radius_m: int
    max_stops: int
    trace_retention_days: int
    history_turns: int
    llm: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = POLICY_FILE, **overrides) -> Policy:
        data = {
            k: v
            for k, v in json.loads(path.read_text(encoding="utf-8")).items()
            if not k.startswith("_")
        }
        data.update(overrides)
        return cls(**data)

    def timeout_for(self, tool: str) -> float:
        return float(self.tool_timeout_s.get(tool, self.tool_timeout_s["default"]))
