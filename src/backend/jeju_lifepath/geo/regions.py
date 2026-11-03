"""Study-area envelopes used for *sanity checks only* (not administrative boundaries).

A dataset claiming to describe Jeju whose points mostly fall outside this
envelope almost certainly has an axis/CRS error or is the wrong dataset.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegionEnvelope:
    name: str
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float
    note: str

    def contains(self, lon: float, lat: float) -> bool:
        return self.min_lon <= lon <= self.max_lon and self.min_lat <= lat <= self.max_lat


# Coarse WGS84 envelope of Jeju Special Self-Governing Province including outer
# islands (Marado ~33.11N in the south, Chujado ~33.9-34.0N in the north,
# Chagwido ~126.15E in the west, Udo ~126.97E in the east) plus a small margin.
JEJU_PROVINCE = RegionEnvelope(
    name="jeju_province",
    min_lon=126.08,
    min_lat=33.06,
    max_lon=127.00,
    max_lat=34.05,
    note="coarse envelope incl. Marado/Chujado/Udo; sanity check only",
)
