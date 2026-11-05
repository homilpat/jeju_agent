"""경로 결과 계약 (ARCHITECTURE §4)."""

from enum import Enum

from pydantic import BaseModel


class RouteStatus(str, Enum):
    OK = "OK"
    UNVERIFIED = "UNVERIFIED"
    NO_FEASIBLE_ROUTE = "NO_FEASIBLE_ROUTE"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class RouteResult(BaseModel):
    status: RouteStatus
    route_edge_ids: list[str] = []
    length_m: float | None = None
    estimated_minutes: float | None = None
    indicator_breakdown: dict = {}
    coverage: dict = {}
    provenance: list[str] = []
    caveats: list[str] = []
