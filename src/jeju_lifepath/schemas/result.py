"""경로 결과 계약 (ARCHITECTURE §4)."""

from enum import Enum

from pydantic import BaseModel


class RouteStatus(str, Enum):
    OK = "OK"
    UNVERIFIED = "UNVERIFIED"
    NO_FEASIBLE_ROUTE = "NO_FEASIBLE_ROUTE"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class RouteResult(BaseModel):
    """내부 결과. caveats만 사용자 문구이며, 나머지는 화면에 그대로 노출하지 않는다."""

    status: RouteStatus
    route_edge_ids: list[str] = []
    unverified_edge_ids: list[str] = []
    length_m: float | None = None
    estimated_minutes: float | None = None
    indicator_breakdown: dict = {}
    coverage: dict = {}
    provenance: list[str] = []
    caveats: list[str] = []  # 사용자에게 보여줄 한국어 문구. ID·enum·JSON 금지
