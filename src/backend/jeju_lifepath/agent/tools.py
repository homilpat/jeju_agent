"""도구 레지스트리와 실행기(AG-201, AG-202, AG-305, AG-501).

도구마다 설명·입력 스키마·읽기/변경 구분·timeout·재시도 정책을 선언하고, 실행 전에 인자를 검증한다.
사용자 ID·권한은 인증 세션에서 실행기로 주입되며 도구 인자로 받지 않는다.
도구 인자로 SQL·셸·임의 URL을 받지 않는다(카카오·경로 엔진 호출만).

기존 함수 대응표(12.2 "정규 이름 하나"):
  search_places          -> kakao.KakaoLocal.keyword
  resolve_location       -> KakaoLocal.address + keyword (지명 -> 좌표, 모호하면 후보)
  find_nearby_facilities -> 약국: 카카오 분류 검색 / 화장실: api.app.toilets_in + 주소 검색
                            / 와이파이·경찰: api.app.nearest(공공데이터)
  compare_routes         -> routing.walk.WalkRouter.plan + presentation.route.render_plan
  get_travel_matrix      -> WalkRouter.distances_from (§11.4 두 번 탐색)
  plan_itinerary         -> agent.itinerary.plan_greedy / apply_changes
  validate_itinerary     -> agent.itinerary.validate
  apply_trip_patch       -> api.store.Store.commit_trip (원자적 버전 검사)
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jeju_lifepath.agent.models import Facility, Policy, ToolError
from jeju_lifepath.schemas import MobilityProfile

LonLat = tuple[float, float]
JEJU_LON = (125.9, 127.0)
JEJU_LAT = (33.0, 34.05)


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lon: float = Field(ge=JEJU_LON[0], le=JEJU_LON[1])
    lat: float = Field(ge=JEJU_LAT[0], le=JEJU_LAT[1])


class SearchPlacesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=40)
    # 카카오 분류 그룹 코드(예: CT1 문화시설). 있으면 검색어 대신 분류로, 위치(near·radius_m) 필수
    category: str | None = Field(default=None, pattern=r"^[A-Z]{2}\d$")
    # 일정 후보로 찾을 때: query(종류)의 카카오 분류에 맞는 곳만(이름만 같은 다른 곳 빼기)
    only_kind: bool = False
    near: Point | None = None
    radius_m: int | None = Field(default=None, ge=100, le=20000)


class ResolveLocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=40)


class NearbyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Facility
    at: Point


class CompareRoutesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: Point
    destination: Point
    mobility: MobilityProfile = MobilityProfile.STANDARD
    prefer_cctv: bool = False


class RouteStopsIn(BaseModel):
    """두 곳 사이 걷는 길에 들를 시설(§2 C02). facility 또는 theme(카카오 검색어) 중 하나."""

    model_config = ConfigDict(extra="forbid")
    origin: Point
    destination: Point
    mobility: MobilityProfile = MobilityProfile.STANDARD
    facility: Facility | None = None
    theme: str | None = Field(default=None, min_length=1, max_length=40)


class AreaAccessIn(BaseModel):
    """한 읍면동(행정동 경계) 안 길 교차점에서 가장 가까운 시설까지 걷는 거리(§2 E01)."""

    model_config = ConfigDict(extra="forbid")
    area: str = Field(min_length=1, max_length=20)  # 읍면동 이름(경계 자료의 이름과 같아야 함)
    facility: Facility
    mobility: MobilityProfile = MobilityProfile.STANDARD


class OlleIn(BaseModel):
    """올레 코스 안내. course가 있으면 그 코스("7", "7-1"), 없으면 near에서 가까운 코스."""

    model_config = ConfigDict(extra="forbid")
    course: str | None = Field(default=None, min_length=1, max_length=10)
    near: Point | None = None


class BarrierFreeIn(BaseModel):
    """무장애 여행지. name이 있으면 그 장소의 상세, 없으면 near에서 가까운 곳."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=40)
    near: Point | None = None


class MatrixIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Point
    targets: list[Point] = Field(max_length=40)
    mobility: MobilityProfile = MobilityProfile.STANDARD


class AnyIn(BaseModel):
    """순수 계산 단계(일정 짜기·검증)의 입력은 실행기 안에서 만든 값이라 형태만 확인한다."""

    model_config = ConfigDict(extra="allow")


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[BaseModel], Any]
    read_only: bool = True
    retryable: bool = True  # 일시 장애일 때 제한적 재시도 허용(읽기 조회만)


class BudgetExceeded(Exception):
    def __init__(self, kind: str):  # "deadline" | "tool_calls" | "cancelled" | "same_failure"
        super().__init__(kind)
        self.kind = kind


def _mask(value):
    """기록용 인자: 좌표는 남기지 않는다. 카카오에서 받은 좌표는 줄여도 저장하면 안 되고
    (카카오 운영정책, 데브톡 152854), 사용자 위치와 구분할 수 없어 모두 뺀다(AG-406, AG-605)."""
    if isinstance(value, dict):
        return {k: (None if k in ("lat", "lon") else _mask(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value[:10]]
    return value


def _summary(result) -> dict:
    """기록용 결과 요약: 개수·상태만(이름·주소·좌표는 남기지 않음)."""
    if isinstance(result, dict):
        out = {}
        for k, v in result.items():
            if isinstance(v, list):
                out[f"{k}_count"] = len(v)
            elif isinstance(v, (bool, int)) or (isinstance(v, str) and k == "status"):  # 좌표(실수)는 빼기
                out[k] = v
        return out
    if isinstance(result, list):
        return {"count": len(result)}
    return {}


@dataclass
class Budget:
    policy: Policy
    started: float = field(default_factory=time.monotonic)
    tool_calls: int = 0
    llm_calls: int = 0
    failures: dict = field(default_factory=dict)

    def remaining_s(self) -> float:
        return self.policy.deadline_s - (time.monotonic() - self.started)


class ToolRunner:
    """한 실행(run)의 도구 호출. 호출 수·실행시간·취소를 매번 확인하고 모든 호출을 기록한다."""

    _pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="agent-tool")

    def __init__(
        self,
        specs: dict[str, ToolSpec],
        store,
        user_id: int,
        run_id: str,
        budget: Budget,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.specs = specs
        self.store = store
        self.user_id = user_id
        self.run_id = run_id
        self.budget = budget
        self.trajectory: list[dict] = []  # 평가용: 실제 호출한 도구와 인자(AgentEvals)
        # 평가(합성 세계)에서만 켠다: 도구 결과를 trajectory에 함께 둔다. 실제 서비스에서는 카카오 결과를
        # 요청 뒤에도 쥐고 있지 않게 끈다(카카오 운영정책)
        self.keep_results = False
        self._sleep = sleep

    def check(self) -> None:
        if self.store.cancel_requested(self.user_id, self.run_id):
            raise BudgetExceeded("cancelled")
        if self.budget.remaining_s() <= 0:
            raise BudgetExceeded("deadline")

    def call(self, name: str, args: dict) -> Any:
        spec = self.specs.get(name)
        if spec is None:
            raise ToolError("unsupported", name)
        try:
            parsed = spec.input_model.model_validate(args)
        except ValidationError:
            raise ToolError("invalid_args", name) from None
        plain = parsed.model_dump(mode="json")
        self.trajectory.append({"name": name, "args": plain})
        args_hash = hashlib.sha256(json.dumps(plain, sort_keys=True).encode()).hexdigest()[:16]
        attempt = 0
        while True:
            attempt += 1
            self.check()
            if self.budget.tool_calls >= self.budget.policy.max_tool_calls:
                raise BudgetExceeded("tool_calls")
            self.budget.tool_calls += 1
            inv_id = uuid.uuid4().hex
            self.store.log_invocation_start(
                self.user_id,
                self.run_id,
                {
                    "invocation_id": inv_id,
                    "tool": name,
                    "attempt": attempt,
                    "args_hash": args_hash,
                    "args_masked": _mask(plain),
                    "idempotency_key": f"{self.run_id}:{name}:{args_hash}",
                },
            )
            started = time.monotonic()
            timeout = max(0.1, min(self.budget.policy.timeout_for(name), self.budget.remaining_s()))
            try:
                future = self._pool.submit(spec.fn, parsed)
                try:
                    result = future.result(timeout=timeout)
                except FutureTimeout:
                    # 늦게 끝난 결과는 버린다. 도구는 읽기 전용이라 상태를 바꾸지 못한다(AG-306)
                    raise ToolError("timeout", name) from None
            except ToolError as e:
                self.store.log_invocation_end(
                    inv_id, "failed", e.kind, int((time.monotonic() - started) * 1000), {}
                )
                key = (name, e.kind)
                self.budget.failures[key] = self.budget.failures.get(key, 0) + 1
                if self.budget.failures[key] > self.budget.policy.max_same_failure:
                    raise BudgetExceeded("same_failure") from e
                retry = (
                    spec.retryable
                    and e.kind in ("transient", "timeout")
                    and attempt <= self.budget.policy.max_retries
                )
                wait = e.retry_after or self.budget.policy.retry_backoff_s * attempt
                if not retry or wait >= self.budget.remaining_s():
                    raise
                self._sleep(wait)
                continue
            except Exception as e:  # 도구 안의 예상 못 한 오류: 사용자에게는 일반 문구로
                self.store.log_invocation_end(
                    inv_id, "failed", "internal", int((time.monotonic() - started) * 1000), {}
                )
                raise ToolError("internal", name) from e
            self.store.log_invocation_end(
                inv_id, "ok", None, int((time.monotonic() - started) * 1000), _summary(result)
            )
            if self.keep_results:
                self.trajectory[-1]["result"] = result
            return result
