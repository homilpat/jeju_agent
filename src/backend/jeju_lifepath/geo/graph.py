"""Single pedestrian graph keyed by ``edge_id`` (ARCHITECTURE §2 Graph).

The graph is an immutable wrapper around a NetworkX ``MultiDiGraph`` whose edge
keys are ``RoadEdge.edge_id``. Every downstream layer addresses segments through
these ids; nothing else is allowed to invent segment identities.

Determinism (REQUIREMENTS §8 "동일 질의·고정 데이터·동일 설정은 동일 결과"):
edges are inserted in ``edge_id`` order, and parallel-edge ties are broken by
``(length_m, edge_id)``, so results do not depend on input ordering.

This module does *not* implement CA-MOR. ``shortest_path_by_length`` is the
baseline length-only path used for validation and as the
``shortest_feasible_length`` reference for the detour bound (REQUIREMENTS §4.6).
"""

from __future__ import annotations

import itertools
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass

import networkx as nx

from jeju_lifepath.geo.geodesy import haversine_m, linestring_length_m
from jeju_lifepath.schemas.common import CRS_WGS84
from jeju_lifepath.schemas.edge import RoadEdge

DEFAULT_NODE_TOLERANCE_M = 1.0


class GraphIntegrityError(ValueError):
    """The supplied edges do not form a consistent pedestrian graph."""


class UnknownEdgeError(KeyError):
    """An edge_id that does not exist in the graph was referenced."""


class UnknownNodeError(KeyError):
    """A node id that does not exist in the graph was referenced."""


@dataclass(frozen=True)
class GraphPath:
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    length_m: float


@dataclass(frozen=True)
class PathIssue:
    code: str
    detail: str


@dataclass(frozen=True)
class LengthDiscrepancy:
    edge_id: str
    declared_m: float
    geodesic_m: float

    @property
    def relative_error(self) -> float:
        return abs(self.declared_m - self.geodesic_m) / self.geodesic_m


class PedestrianGraph:
    """Directed pedestrian network. Construct via ``PedestrianGraph(edges)``."""

    crs = CRS_WGS84

    def __init__(
        self,
        edges: Iterable[RoadEdge],
        *,
        version: str | None = None,
        node_tolerance_m: float = DEFAULT_NODE_TOLERANCE_M,
    ) -> None:
        ordered = sorted(edges, key=lambda e: e.edge_id)
        if not ordered:
            raise GraphIntegrityError("a pedestrian graph needs at least one edge")
        self.version = version
        self._edges: dict[str, RoadEdge] = {}
        self._node_coords: dict[str, tuple[float, float]] = {}
        self._g = nx.MultiDiGraph()
        for edge in ordered:
            if edge.edge_id in self._edges:
                raise GraphIntegrityError(f"duplicate edge_id {edge.edge_id!r}")
            coords = edge.geometry.coords
            self._register_node(edge.source_node, coords[0], edge.edge_id, node_tolerance_m)
            self._register_node(edge.target_node, coords[-1], edge.edge_id, node_tolerance_m)
            self._edges[edge.edge_id] = edge
            self._g.add_edge(
                edge.source_node, edge.target_node, key=edge.edge_id, length_m=edge.length_m
            )

    def _register_node(
        self, node_id: str, coord: tuple[float, float], edge_id: str, tol_m: float
    ) -> None:
        lon, lat = float(coord[0]), float(coord[1])
        known = self._node_coords.get(node_id)
        if known is None:
            self._node_coords[node_id] = (lon, lat)
            self._g.add_node(node_id, lon=lon, lat=lat)
            return
        gap = haversine_m(known[0], known[1], lon, lat)
        if gap > tol_m:
            raise GraphIntegrityError(
                f"edge {edge_id!r} endpoint for node {node_id!r} is {gap:.2f} m from the node's "
                f"coordinate (tolerance {tol_m} m): geometry/topology mismatch"
            )

    # ------------------------------------------------------------------ access
    def __len__(self) -> int:
        return len(self._edges)

    def __contains__(self, edge_id: object) -> bool:
        return edge_id in self._edges

    def edge(self, edge_id: str) -> RoadEdge:
        try:
            return self._edges[edge_id]
        except KeyError:
            raise UnknownEdgeError(edge_id) from None

    def edges(self) -> Iterator[RoadEdge]:
        return iter(self._edges.values())

    @property
    def edge_ids(self) -> frozenset[str]:
        return frozenset(self._edges)

    @property
    def node_ids(self) -> frozenset[str]:
        return frozenset(self._node_coords)

    def node_coord(self, node_id: str) -> tuple[float, float]:
        """(lon, lat) of a node."""
        try:
            return self._node_coords[node_id]
        except KeyError:
            raise UnknownNodeError(node_id) from None

    def out_edges(self, node_id: str) -> tuple[RoadEdge, ...]:
        self.node_coord(node_id)
        return tuple(self._edges[k] for _, _, k in self._g.out_edges(node_id, keys=True))

    def with_edges(self, edges: Iterable[RoadEdge]) -> PedestrianGraph:
        """New graph with the same edge_id set but updated edge attributes.

        Used by feature layers so analysers never change graph topology.
        """
        updated = {e.edge_id: e for e in edges}
        if set(updated) != set(self._edges):
            raise GraphIntegrityError("with_edges must keep exactly the same edge_id set")
        for edge_id, new in updated.items():
            old = self._edges[edge_id]
            if (new.source_node, new.target_node) != (old.source_node, old.target_node):
                raise GraphIntegrityError(f"edge {edge_id!r} topology changed")
        return PedestrianGraph(updated.values(), version=self.version)

    # ------------------------------------------------------------- validation
    def _view(self, allowed_edge_ids: Collection[str] | None) -> nx.MultiDiGraph:
        if allowed_edge_ids is None:
            return self._g
        unknown = set(allowed_edge_ids) - self._edges.keys()
        if unknown:
            raise UnknownEdgeError(sorted(unknown))
        allowed = frozenset(allowed_edge_ids)
        return nx.subgraph_view(self._g, filter_edge=lambda _u, _v, k: k in allowed)

    def length_discrepancies(self, rel_tolerance: float = 0.05) -> list[LengthDiscrepancy]:
        """Edges whose declared ``length_m`` deviates from their geometry length."""
        out = []
        for edge in self._edges.values():
            geodesic = linestring_length_m(edge.geometry)
            item = LengthDiscrepancy(edge.edge_id, edge.length_m, geodesic)
            if item.relative_error > rel_tolerance:
                out.append(item)
        return out

    def strongly_connected_components(self) -> list[frozenset[str]]:
        comps = (frozenset(c) for c in nx.strongly_connected_components(self._g))
        return sorted(comps, key=lambda c: (-len(c), sorted(c)))

    def validate_path(self, edge_ids: Iterable[str]) -> list[PathIssue]:
        """Check an edge sequence for existence, continuity and repetition."""
        ids = list(edge_ids)
        if not ids:
            return [PathIssue("EMPTY_PATH", "path has no edges")]
        issues: list[PathIssue] = []
        missing = [e for e in ids if e not in self._edges]
        if missing:
            return [PathIssue("UNKNOWN_EDGE", f"edges not in graph: {missing}")]
        if len(set(ids)) != len(ids):
            issues.append(PathIssue("REPEATED_EDGE", "path traverses an edge more than once"))
        for prev, nxt in itertools.pairwise(ids):
            if self._edges[prev].target_node != self._edges[nxt].source_node:
                issues.append(
                    PathIssue("DISCONTINUOUS", f"{prev!r} does not end where {nxt!r} starts")
                )
        return issues

    def path_length_m(self, edge_ids: Iterable[str]) -> float:
        return sum(self.edge(e).length_m for e in edge_ids)

    # ----------------------------------------------------------------- search
    def is_reachable(
        self, source: str, target: str, allowed_edge_ids: Collection[str] | None = None
    ) -> bool:
        self.node_coord(source)
        self.node_coord(target)
        return nx.has_path(self._view(allowed_edge_ids), source, target)

    def shortest_path_by_length(
        self, source: str, target: str, allowed_edge_ids: Collection[str] | None = None
    ) -> GraphPath | None:
        """Length-only shortest path restricted to ``allowed_edge_ids``.

        Returns ``None`` when no path exists. Callers translate that into an
        explicit NO_FEASIBLE_ROUTE status; it is never silently relaxed here.
        """
        self.node_coord(source)
        self.node_coord(target)
        if source == target:
            raise ValueError("source and target must differ")
        view = self._view(allowed_edge_ids)

        def best(u: str, v: str) -> str:
            return min(view[u][v], key=lambda k: (view[u][v][k]["length_m"], k))

        def weight(u: str, v: str, _data: object) -> float:
            return view[u][v][best(u, v)]["length_m"]

        try:
            nodes = nx.dijkstra_path(view, source, target, weight=weight)
        except nx.NetworkXNoPath:
            return None
        edge_ids = tuple(best(u, v) for u, v in itertools.pairwise(nodes))
        return GraphPath(tuple(nodes), edge_ids, self.path_length_m(edge_ids))
