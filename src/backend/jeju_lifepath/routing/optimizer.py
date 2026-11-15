"""CA-MOR route search (B02/B03; REQUIREMENTS §4.5–4.7).

Minimum cost under the detour bound: simple paths are enumerated in ascending
cost and the first one within ``(1 + detour_ratio) * shortest_feasible_length``
is chosen. If found within ``MAX_CANDIDATES`` it is the bounded minimum-cost path;
otherwise the shortest feasible path is returned with a caveat.
"""

from __future__ import annotations

from itertools import islice, pairwise

import networkx as nx

from jeju_lifepath.geo.graph import PedestrianGraph
from jeju_lifepath.routing.constraints import ConstraintEngine, ConstraintProfile
from jeju_lifepath.routing.cost import (
    IndicatorDirection,
    WeightBasis,
    WeightSet,
    compute_edge_cost,
)
from jeju_lifepath.schemas import RoadEdge, RoutePreference, RouteRequest, RouteResult, RouteStatus

MAX_CANDIDATES = 200
WALK_M_PER_MIN = 75.0  # 4.5 km/h
# q when no weighted indicator is observed. 0 would make data-less edges look safest.
NO_DATA_Q = 0.5

PROVISIONAL_VERSION = "provisional-v0"
_ADV, _FAV = IndicatorDirection.ADVERSE, IndicatorDirection.FAVOURABLE
# shortcut: unvalidated manual weights/lambda, replace with G4 evidence + sensitivity results.
# activity_index is excluded: its direction depends on user purpose (REQUIREMENTS §5).
_WEIGHTS = {
    "cctv_index": (1.0, _FAV),
    "lighting_index": (1.0, _FAV),
    "crime_context": (1.0, _ADV),
    "traffic_exposure": (1.0, _ADV),
    "weather_exposure": (0.5, _ADV),
}
_LAMBDA = {
    RoutePreference.SHORTEST: 0.0,
    RoutePreference.BALANCED: 1.0,
    RoutePreference.SAFETY: 3.0,
}
PREF_LABEL = {
    RoutePreference.SHORTEST: "최단",
    RoutePreference.BALANCED: "균형",
    RoutePreference.SAFETY: "안전환경 우선",
}


def provisional_weight_set(pref: RoutePreference) -> WeightSet:
    return WeightSet(
        version=PROVISIONAL_VERSION,
        basis=WeightBasis.PROVISIONAL_MANUAL,
        penalty_lambda=_LAMBDA[pref],
        weights={k: w for k, (w, _) in _WEIGHTS.items()},
        directions={k: d for k, (_, d) in _WEIGHTS.items()},
        provenance=("src/backend/jeju_lifepath/routing/optimizer.py",),
        notes=("not validated on Jeju data",),
    )


def _q(edge: RoadEdge, ws: WeightSet) -> tuple[float, float]:
    c = compute_edge_cost(edge, ws)
    return (NO_DATA_Q if c.adverse_score is None else c.adverse_score), c.coverage


def _collapse(edges: list[RoadEdge], ws: WeightSet) -> nx.DiGraph:
    # shortcut: keeps only the cheapest parallel edge per node pair; extend if parallels matter
    d = nx.DiGraph()
    for e in sorted(edges, key=lambda e: e.edge_id):
        cost = e.length_m * (1 + ws.penalty_lambda * _q(e, ws)[0])
        if (
            not d.has_edge(e.source_node, e.target_node)
            or cost < d[e.source_node][e.target_node]["cost"]
        ):
            d.add_edge(e.source_node, e.target_node, cost=cost, edge=e)
    return d


def find_route(
    graph: PedestrianGraph,
    req: RouteRequest,
    engine: ConstraintEngine | None = None,
    weight_set: WeightSet | None = None,
) -> RouteResult:
    if not isinstance(req.origin, str) or not isinstance(req.destination, str):
        return RouteResult(
            status=RouteStatus.DATA_UNAVAILABLE,
            caveats=("좌표로 입력한 위치는 아직 경로 검색을 지원하지 않습니다.",),
        )
    if req.origin not in graph.node_ids or req.destination not in graph.node_ids:
        return RouteResult(
            status=RouteStatus.DATA_UNAVAILABLE,
            caveats=("출발지나 도착지를 지도에서 찾지 못했습니다.",),
        )
    ws = weight_set or provisional_weight_set(req.route_preference)
    evaluation = (engine or ConstraintEngine()).evaluate(
        graph.edges(), ConstraintProfile.from_request(req)
    )
    allowed = evaluation.searchable_edge_ids
    baseline = graph.shortest_path_by_length(req.origin, req.destination, allowed)
    if baseline is None:
        return _no_route(evaluation.profile.verified_only)
    max_len = baseline.length_m * (1 + req.detour_ratio)

    d = _collapse([graph.edge(i) for i in allowed], ws)
    chosen = None
    for nodes in islice(
        nx.shortest_simple_paths(d, req.origin, req.destination, "cost"), MAX_CANDIDATES
    ):
        edges = [d[u][v]["edge"] for u, v in pairwise(nodes)]
        if sum(e.length_m for e in edges) <= max_len + 1e-9:
            chosen = edges
            break
    caveats = []
    if chosen is None:
        chosen = [graph.edge(i) for i in baseline.edge_ids]
        caveats.append("허용한 우회 범위 안에서 더 나은 경로를 찾지 못해 최단 경로를 안내합니다.")
    unknown = evaluation.unknown_edge_ids
    return _result(chosen, ws, baseline.length_m, caveats, unknown)


def _no_route(verified_only: bool) -> RouteResult:
    msg = (
        "통행 가능이 확인된 구간만으로 연결되는 경로가 없습니다."
        if verified_only
        else "통행 가능한 경로가 없습니다."
    )
    return RouteResult(status=RouteStatus.NO_FEASIBLE_ROUTE, caveats=(msg,))


def _lw_mean(values: list[tuple[float, float]]) -> float | None:
    """Length-weighted mean of (value, length) pairs."""
    w = sum(lm for _, lm in values)
    return sum(v * lm for v, lm in values) / w if w else None


def _result(
    edges: list[RoadEdge],
    ws: WeightSet,
    base_len: float,
    caveats: list[str],
    unknown_ids: frozenset[str],
) -> RouteResult:
    length = sum(e.length_m for e in edges)
    unverified = tuple(e.edge_id for e in edges if e.edge_id in unknown_ids)
    if unverified:
        caveats.append(
            f"통행 가능 여부가 확인되지 않은 구간 {len(unverified)}곳이 포함되어 있습니다."
        )
    names = tuple(_WEIGHTS)
    breakdown = {
        n: _lw_mean([(getattr(e, n), e.length_m) for e in edges if getattr(e, n) is not None])
        for n in names
    }
    breakdown["adverse_q"] = _lw_mean([(_q(e, ws)[0], e.length_m) for e in edges])
    coverage = {
        "indicator_coverage": _lw_mean([(_q(e, ws)[1], e.length_m) for e in edges]) or 0.0,
        **{n: sum(e.length_m for e in edges if getattr(e, n) is not None) / length for n in names},
    }
    return RouteResult(
        status=RouteStatus.UNVERIFIED if unverified else RouteStatus.OK,
        route_edge_ids=tuple(e.edge_id for e in edges),
        unverified_edge_ids=unverified,
        length_m=length,
        estimated_minutes=round(length / WALK_M_PER_MIN, 1),
        indicator_breakdown=breakdown,
        coverage=coverage,
        provenance=tuple(sorted({s for e in edges for s in e.source_ids})),
        shortest_feasible_m=base_len,
        weights_version=ws.version,
        caveats=tuple(caveats),
    )


def compare_routes(graph: PedestrianGraph, req: RouteRequest) -> dict[RoutePreference, RouteResult]:
    """Shortest/balanced/safety candidates. Identical routes are flagged, not faked."""
    out: dict[RoutePreference, RouteResult] = {}
    for pref in (RoutePreference.SHORTEST, RoutePreference.BALANCED, RoutePreference.SAFETY):
        r = find_route(graph, req.model_copy(update={"route_preference": pref}))
        for other_pref, other in out.items():
            if r.route_edge_ids and r.route_edge_ids == other.route_edge_ids:
                note = f"{PREF_LABEL[other_pref]} 경로와 같습니다."
                r = r.model_copy(update={"caveats": (*r.caveats, note)})
                break
        out[pref] = r
    return out
