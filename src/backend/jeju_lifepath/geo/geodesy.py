"""Geodesic helpers for WGS84 (lon, lat) geometry. Distances in metres."""

from __future__ import annotations

import itertools
import math

from shapely import LineString

# IUGG mean Earth radius. Spherical haversine error (<0.5%) is acceptable for
# consistency checks; projected-CRS lengths come from the graph builder (G2).
EARTH_MEAN_RADIUS_M = 6_371_008.8


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_MEAN_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def linestring_length_m(line: LineString) -> float:
    coords = list(line.coords)
    return sum(haversine_m(x1, y1, x2, y2) for (x1, y1), (x2, y2) in itertools.pairwise(coords))
