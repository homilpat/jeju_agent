"""SYNTHETIC test graph — NOT Jeju official data (REQUIREMENTS §3: DEMO only).

Topology (two-way footways; each segment = two directed RoadEdges)::

    A ─── B ─── C
    │     │     │
    D ─── E ─── F

Node coordinates are invented, placed near Jeju-si only so that distances are
realistic metres. Edge lengths are computed from geometry (haversine) so the
graph passes geometry/length consistency checks. Every edge carries the
``SYNTHETIC_SOURCE_ID`` provenance and a DEMO caveat so it can never be mistaken
for official data.

Baseline facts the tests rely on (derived from geometry, not hard-coded paths):
    A→C shortest: A-B-C
    D is placed further south so A-D-E is longer than A-B-E.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jeju_lifepath.geo.geodesy import linestring_length_m
from jeju_lifepath.geo.graph import PedestrianGraph
from jeju_lifepath.schemas import ClosureStatus, Passability, Quality, RoadEdge
from jeju_lifepath.schemas.common import to_wgs84_linestring

SYNTHETIC_SOURCE_ID = "synthetic:g0-toy-grid-v1"
SYNTHETIC_CAVEAT = "DEMO: synthetic test graph, not official Jeju data"

NODES: Mapping[str, tuple[float, float]] = {  # (lon, lat)
    "A": (126.5300, 33.5000),
    "B": (126.5310, 33.5000),
    "C": (126.5320, 33.5000),
    "D": (126.5300, 33.4985),
    "E": (126.5310, 33.4990),
    "F": (126.5320, 33.4990),
}

SEGMENTS: tuple[str, ...] = ("AB", "BC", "AD", "BE", "CF", "DE", "EF")


def directed_ids(segment: str) -> tuple[str, str]:
    u, v = segment
    return f"{u}-{v}", f"{v}-{u}"


def make_edge(edge_id: str, u: str, v: str, **attrs: Any) -> RoadEdge:
    geometry = to_wgs84_linestring([NODES[u], NODES[v]])
    base: dict[str, Any] = {
        "edge_id": edge_id,
        "source_node": u,
        "target_node": v,
        "geometry": geometry,
        "length_m": linestring_length_m(geometry),
        "accessibility": Passability.PASS,
        "closure_status": ClosureStatus.OPEN,
        "source_ids": (SYNTHETIC_SOURCE_ID,),
        "quality": Quality(caveats=(SYNTHETIC_CAVEAT,)),
    }
    base.update(attrs)
    return RoadEdge(**base)


def build_edges(
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[RoadEdge]:
    """Edges of the grid. ``overrides`` maps a segment ("BC") or directed id
    ("B-C") to RoadEdge attributes; a segment override applies both ways."""
    overrides = overrides or {}
    unknown = set(overrides) - set(SEGMENTS) - {i for s in SEGMENTS for i in directed_ids(s)}
    if unknown:
        raise KeyError(f"unknown segment/edge in overrides: {sorted(unknown)}")
    edges = []
    for seg in SEGMENTS:
        u, v = seg
        for edge_id, (a, b) in zip(directed_ids(seg), ((u, v), (v, u)), strict=True):
            attrs = {**overrides.get(seg, {}), **overrides.get(edge_id, {})}
            edges.append(make_edge(edge_id, a, b, **attrs))
    return edges


def build_graph(overrides: Mapping[str, Mapping[str, Any]] | None = None) -> PedestrianGraph:
    return PedestrianGraph(build_edges(overrides), version=SYNTHETIC_SOURCE_ID)
