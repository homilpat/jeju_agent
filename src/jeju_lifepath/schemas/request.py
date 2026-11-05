"""경로 요청 계약 (ARCHITECTURE §4)."""

from enum import Enum

from pydantic import BaseModel, Field


class RoutePreference(str, Enum):
    SHORTEST = "SHORTEST"
    SAFETY = "SAFETY"
    BALANCED = "BALANCED"


class RouteRequest(BaseModel):
    origin: tuple[float, float] | str
    destination: tuple[float, float] | str
    departure_time: str | None = None
    route_preference: RoutePreference
    mobility_profile: str = "DEFAULT"
    avoid_stairs: bool = False
    detour_ratio: float = Field(default=0.2, ge=0, le=0.5)
    poi_categories: list[str] = []
    require_verified_accessibility: bool = False
