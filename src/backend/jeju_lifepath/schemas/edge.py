"""RoadEdge — the single shared road-segment contract (REQUIREMENTS §3).

Every analyzer (A01–A07), the constraint engine (B01), the router (B02/B03),
POI/Wi-Fi (C01–C03, D01) and the gap analysis (E01) refer to road segments
exclusively through ``RoadEdge.edge_id``.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AwareDatetime, Field, field_validator

from jeju_lifepath.schemas.common import (
    ClosureStatus,
    FrozenModel,
    NonEmptyStr,
    Passability,
    PositiveMetres,
    UnitInterval,
    WGS84LineString,
)

# Numeric [0, 1] indicators carried on each edge, keyed by RoadEdge field name.
# Values are *normalised indicators*, not probabilities. Their direction
# (favourable vs adverse) is NOT fixed here — it is declared by the weight set
# used for costing (see routing/cost.py), because e.g. activity_index means
# "isolation" for one user goal and "crowding" for another (REQUIREMENTS §5).
INDICATOR_FIELDS: tuple[str, ...] = (
    "cctv_index",
    "lighting_index",
    "crime_context",
    "activity_index",
    "traffic_exposure",
    "weather_exposure",
    # D01: AP installation-location proximity only — never signal/continuity.
    "wifi_proximity_index",
)

# Categorical status fields used by the constraint engine.
STATUS_FIELDS: tuple[str, ...] = ("accessibility", "closure_status")

FEATURE_FIELDS: tuple[str, ...] = INDICATOR_FIELDS + STATUS_FIELDS


class Resolution(FrozenModel):
    """Spatial/temporal granularity of the underlying data, e.g. '읍면동', 'hourly'."""

    spatial: NonEmptyStr | None = None
    temporal: NonEmptyStr | None = None


class Quality(FrozenModel):
    coverage: UnitInterval | None = None
    caveats: tuple[NonEmptyStr, ...] = ()


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


class RoadEdge(FrozenModel):
    """A directed pedestrian road segment.

    Directionality: one RoadEdge is traversable from ``source_node`` to
    ``target_node`` only. A two-way footway is represented by two edges with
    distinct ``edge_id`` (same convention as OSMnx walk graphs).

    ``None`` indicator values mean *unavailable*, never "risk 0".
    """

    edge_id: NonEmptyStr
    source_node: NonEmptyStr
    target_node: NonEmptyStr
    geometry: WGS84LineString
    length_m: PositiveMetres

    cctv_index: UnitInterval | None = None
    lighting_index: UnitInterval | None = None
    crime_context: UnitInterval | None = None
    activity_index: UnitInterval | None = None
    traffic_exposure: UnitInterval | None = None
    weather_exposure: UnitInterval | None = None
    wifi_proximity_index: UnitInterval | None = None

    # Defaults are UNKNOWN: absence of evidence is not evidence of passability.
    accessibility: Passability = Passability.UNKNOWN
    closure_status: ClosureStatus = ClosureStatus.UNKNOWN

    observed_at: AwareDatetime | None = None
    source_ids: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
    resolution: Resolution = Resolution()
    quality: Quality = Quality()

    @field_validator("source_ids")
    @classmethod
    def _unique_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _dedupe(value)

    def indicator_values(self) -> dict[str, float | None]:
        """All numeric indicators keyed by field name (None = unavailable)."""
        return {name: getattr(self, name) for name in INDICATOR_FIELDS}

    def available_indicators(self) -> tuple[str, ...]:
        return tuple(name for name, v in self.indicator_values().items() if v is not None)
