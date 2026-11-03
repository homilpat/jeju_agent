"""Shared enums, constrained types and geometry handling for all schemas.

Coordinate convention (fixed project-wide, REQUIREMENTS §3):
  * CRS is WGS84 (EPSG:4326).
  * Geometry coordinates are (lon, lat) — shapely x/y order.
  * Point-like user inputs use the explicit ``GeoPoint(lat, lon)`` model so that
    axis order is never guessed from a bare tuple.
  * Distances are metres; durations are minutes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    StringConstraints,
    WithJsonSchema,
)
from shapely import LineString
from shapely.errors import ShapelyError

CRS_WGS84 = "EPSG:4326"

LON_RANGE = (-180.0, 180.0)
LAT_RANGE = (-90.0, 90.0)


class Passability(StrEnum):
    """Tri-state used for edge accessibility and constraint verdicts.

    UNKNOWN is never equivalent to PASS (REQUIREMENTS §4.3, ARCHITECTURE §5).
    """

    PASS = "PASS"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"


class ClosureStatus(StrEnum):
    """Official closure state. A failed update must be UNKNOWN, never OPEN."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class RoutePreference(StrEnum):
    SHORTEST = "SHORTEST"
    SAFETY = "SAFETY"
    BALANCED = "BALANCED"


class MobilityProfile(StrEnum):
    """User-declared mobility condition. Never inferred from user traits (ARCHITECTURE §7)."""

    STANDARD = "STANDARD"
    WHEELCHAIR = "WHEELCHAIR"
    STROLLER = "STROLLER"


class RouteStatus(StrEnum):
    OK = "OK"
    UNVERIFIED = "UNVERIFIED"
    NO_FEASIBLE_ROUTE = "NO_FEASIBLE_ROUTE"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
PositiveMetres = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]


class FrozenModel(BaseModel):
    """Base for immutable, strictly-shaped contract models."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class GeoPoint(FrozenModel):
    """A WGS84 point with explicit axis names."""

    lat: float = Field(ge=LAT_RANGE[0], le=LAT_RANGE[1], allow_inf_nan=False)
    lon: float = Field(ge=LON_RANGE[0], le=LON_RANGE[1], allow_inf_nan=False)


def _check_lon_lat(lon: float, lat: float) -> None:
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise ValueError("geometry coordinates must be finite")
    if not (LON_RANGE[0] <= lon <= LON_RANGE[1]):
        raise ValueError(f"longitude {lon} outside {LON_RANGE}; geometry must be (lon, lat) WGS84")
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1]):
        raise ValueError(f"latitude {lat} outside {LAT_RANGE}; geometry must be (lon, lat) WGS84")


def to_wgs84_linestring(value: Any) -> LineString:
    """Validate/convert a value into a 2D WGS84 (lon, lat) LineString."""
    if isinstance(value, LineString):
        line = value
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        try:
            line = LineString([tuple(coord) for coord in value])
        except (ShapelyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid LineString coordinates: {exc}") from exc
    else:
        raise ValueError("geometry must be a shapely LineString or a sequence of (lon, lat)")
    if line.is_empty or len(line.coords) < 2:  # noqa: PLR2004 - a line needs two vertices
        raise ValueError("geometry must contain at least two vertices")
    if line.has_z:
        raise ValueError("geometry must be 2D (lon, lat)")
    for lon, lat in line.coords:
        _check_lon_lat(lon, lat)
    if line.length == 0.0:
        raise ValueError("geometry is degenerate (zero length)")
    return line


def _linestring_to_coords(line: LineString) -> list[list[float]]:
    return [[float(x), float(y)] for x, y in line.coords]


WGS84LineString = Annotated[
    LineString,
    PlainValidator(to_wgs84_linestring),
    PlainSerializer(_linestring_to_coords, return_type=list),
    WithJsonSchema(
        {
            "type": "array",
            "description": "WGS84 LineString as [[lon, lat], ...]",
            "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            "minItems": 2,
        }
    ),
]
