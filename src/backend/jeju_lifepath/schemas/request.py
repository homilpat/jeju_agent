"""RouteRequest — validated routing input (ARCHITECTURE §4).

This is the *resolved* request handed to the routing tools. Slot filling and
clarification questions (e.g. asking SHORTEST/SAFETY/BALANCED) happen in the
dialogue layer (G9) before a RouteRequest is constructed, which is why
``route_preference`` has no default here.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import AwareDatetime, BeforeValidator, Field, field_validator, model_validator

from jeju_lifepath.schemas.common import (
    FrozenModel,
    GeoPoint,
    MobilityProfile,
    NonEmptyStr,
    RoutePreference,
)

# Initial detour policy bound from ARCHITECTURE §4 ("0~0.50 initial policy").
DETOUR_RATIO_POLICY_MAX = 0.50


def _coerce_point(value: Any) -> Any:
    """Accept a (lat, lon) 2-tuple as documented shorthand for GeoPoint.

    Strings are left untouched so they are treated as place queries for
    resolve_place (G9); dicts/GeoPoint go through normal validation.
    """
    if isinstance(value, (tuple, list)):
        if len(value) != 2:  # noqa: PLR2004 - (lat, lon)
            raise ValueError("coordinate tuple must be (lat, lon)")
        return GeoPoint(lat=value[0], lon=value[1])
    return value


Location = Annotated[GeoPoint | NonEmptyStr, BeforeValidator(_coerce_point)]


class RouteRequest(FrozenModel):
    origin: Location
    destination: Location
    departure_time: AwareDatetime | None = None
    route_preference: RoutePreference
    mobility_profile: MobilityProfile = MobilityProfile.STANDARD
    avoid_stairs: bool = False
    detour_ratio: float = Field(ge=0.0, le=DETOUR_RATIO_POLICY_MAX, allow_inf_nan=False)
    poi_categories: tuple[NonEmptyStr, ...] = ()
    require_verified_accessibility: bool = False

    @field_validator("poi_categories")
    @classmethod
    def _normalise_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # Shared category filter for C01–C03 (no per-category subsystems).
        return tuple(dict.fromkeys(v.lower() for v in value))

    @model_validator(mode="after")
    def _distinct_endpoints(self) -> RouteRequest:
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        return self
