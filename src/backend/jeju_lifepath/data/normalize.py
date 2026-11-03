"""Normalise tabular point data to WGS84 (lon, lat) with explicit row rejections.

Rows are never "repaired" by guessing: a suspected lat/lon swap is rejected and
counted, not silently swapped back (the cause must be fixed in the spec).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from pyproj import CRS, Transformer

from jeju_lifepath.data.registry import PointColumnMapping
from jeju_lifepath.geo.regions import RegionEnvelope
from jeju_lifepath.schemas.common import CRS_WGS84, LAT_RANGE, LON_RANGE


class RejectReason(StrEnum):
    MISSING_COORDINATE = "MISSING_COORDINATE"
    NON_NUMERIC_COORDINATE = "NON_NUMERIC_COORDINATE"
    SUSPECTED_AXIS_SWAP = "SUSPECTED_AXIS_SWAP"
    OUT_OF_CRS_RANGE = "OUT_OF_CRS_RANGE"
    DUPLICATE_ID = "DUPLICATE_ID"


@dataclass(frozen=True)
class NormalizedPoint:
    record_id: str
    lon: float
    lat: float
    attributes: Mapping[str, str | None]


@dataclass(frozen=True)
class PointNormalization:
    points: tuple[NormalizedPoint, ...]
    rejected: Mapping[RejectReason, int]
    missing_rates: Mapping[str, float]
    in_region_ratio: float | None
    row_count: int
    rejected_rows: tuple[tuple[int, RejectReason], ...] = field(default=(), repr=False)


def _in_lon_lat(lon: float, lat: float) -> bool:
    return LON_RANGE[0] <= lon <= LON_RANGE[1] and LAT_RANGE[0] <= lat <= LAT_RANGE[1]


def _parse_float(value: str) -> float | None:
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _missing_rates(rows: Sequence[Mapping[str, str]], columns: Sequence[str]) -> dict[str, float]:
    if not rows:
        return dict.fromkeys(columns, 0.0)
    return {c: sum(1 for r in rows if not r.get(c, "").strip()) / len(rows) for c in columns}


def normalize_points(
    rows: Sequence[Mapping[str, str]],
    mapping: PointColumnMapping,
    source_crs: str,
    region: RegionEnvelope | None = None,
) -> PointNormalization:
    crs = CRS.from_user_input(source_crs)
    geographic = crs.is_geographic
    transformer = (
        None
        if crs == CRS.from_user_input(CRS_WGS84)
        else Transformer.from_crs(crs, CRS_WGS84, always_xy=True)
    )
    points: list[NormalizedPoint] = []
    rejected: list[tuple[int, RejectReason]] = []
    seen_ids: set[str] = set()

    for index, row in enumerate(rows):
        raw_x, raw_y = row.get(mapping.x_column, ""), row.get(mapping.y_column, "")
        if not raw_x or not raw_y:
            rejected.append((index, RejectReason.MISSING_COORDINATE))
            continue
        x, y = _parse_float(raw_x), _parse_float(raw_y)
        if x is None or y is None:
            rejected.append((index, RejectReason.NON_NUMERIC_COORDINATE))
            continue
        if geographic and not _in_lon_lat(x, y):
            reason = (
                RejectReason.SUSPECTED_AXIS_SWAP
                if _in_lon_lat(y, x)
                else RejectReason.OUT_OF_CRS_RANGE
            )
            rejected.append((index, reason))
            continue
        lon, lat = transformer.transform(x, y) if transformer else (x, y)
        if not (math.isfinite(lon) and math.isfinite(lat) and _in_lon_lat(lon, lat)):
            rejected.append((index, RejectReason.OUT_OF_CRS_RANGE))
            continue
        record_id = row.get(mapping.id_column, "") if mapping.id_column else ""
        record_id = record_id or f"row-{index + 1}"
        if record_id in seen_ids:
            rejected.append((index, RejectReason.DUPLICATE_ID))
            continue
        seen_ids.add(record_id)
        attributes = {
            canonical: (row.get(source) or None) for canonical, source in mapping.attributes.items()
        }
        points.append(NormalizedPoint(record_id, float(lon), float(lat), attributes))

    in_region = None
    if region is not None and points:
        in_region = sum(region.contains(p.lon, p.lat) for p in points) / len(points)
    counts = Counter(reason for _, reason in rejected)
    return PointNormalization(
        points=tuple(points),
        rejected={r: counts[r] for r in RejectReason if counts[r]},
        missing_rates=_missing_rates(rows, mapping.source_columns()),
        in_region_ratio=in_region,
        row_count=len(rows),
        rejected_rows=tuple(rejected),
    )
