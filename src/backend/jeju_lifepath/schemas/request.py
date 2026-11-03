"""RouteRequest — validated routing input (ARCHITECTURE §4).

This is the *resolved* request handed to the routing tools. Slot filling and
clarification questions (e.g. asking SHORTEST/SAFETY/BALANCED) happen in the
dialogue layer (G9) before a RouteRequest is constructed, which is why
``route_preference`` has no default here.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AwareDatetime, BeforeValidator, Field, field_validator, model_validator

from jeju_lifepath.schemas.common import (
    FrozenModel,
    GeoPoint,
    MobilityProfile,
    NonEmptyStr,
    RoutePreference,
    coerce_point,
)

# Initial detour policy bound from ARCHITECTURE §4 ("0~0.50 initial policy").
DETOUR_RATIO_POLICY_MAX = 0.50


Location = Annotated[GeoPoint | NonEmptyStr, BeforeValidator(coerce_point)]


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
