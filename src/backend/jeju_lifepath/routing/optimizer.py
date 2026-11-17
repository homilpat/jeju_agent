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
from jeju_lifepath.schemas import (
    DETOUR_RATIO_POLICY_MAX,
    MobilityProfile,
    RoadEdge,
    RoutePreference,
    RouteRequest,
    RouteResult,
    RouteStatus,
)

# shortcut: algorithm budget, not an empirical value; set from a timing run on the real
# Jeju graph (G2) and report how often the fallback fires.
MAX_CANDIDATES = 200

# Usual outdoor walking pace of healthy adults 1.31 m/s (meta-analysis, Murtagh et al. 2021,
# Sports Med., PMID 33030707). Manual wheelchair self-selected 1.04 m/s (170 experienced
# users, PMC4631660) was measured on a stationary ergometer, so outdoor use is unverified.
# No walking-pace study found for strollers: STROLLER uses the adult value (unverified).
SPEED_M_PER_MIN = {
    MobilityProfile.STANDARD: 1.31 * 60,
    MobilityProfile.STROLLER: 1.31 * 60,
    MobilityProfile.WHEELCHAIR: 1.04 * 60,
}

PROVISIONAL_VERSION = "provisional-v1"
_ADV, _FAV = IndicatorDirection.ADVERSE, IndicatorDirection.FAVOURABLE
# Equal (unit) weights: without Jeju-validated estimates, unit weighting is the robust
# default (Dawes 1979, Am. Psychol. 34:571). Paper effect sizes are never used as
# weights (REQUIREMENTS §5). activity_index is excluded: its direction depends on
# user purpose (REQUIREMENTS §5). Directions follow REQUIREMENTS §10 references.
_WEIGHTS = {
    "cctv_index": (1.0, _FAV),
    "lighting_index": (1.0, _FAV),
    "crime_context": (1.0, _ADV),
    "traffic_exposure": (1.0, _ADV),
    "weather_exposure": (1.0, _ADV),
}
# lambda: a path of ratio r longer with adverse score q1 beats one with q0 iff
# r < (1 + lambda*q0) / (1 + lambda*q1) - 1, so lambda is the largest detour ratio taken
# to go from q=1 to q=0. SAFETY uses the whole policy detour cap, BALANCED half of it.
# shortcut: no empirical basis (the 0.50 cap itself is an initial policy); estimate from
# Jeju route-choice data with the Basu & Sevtsuk (2022) method when available.
_LAMBDA = {
    RoutePreference.SHORTEST: 0.0,
    RoutePreference.BALANCED: DETOUR_RATIO_POLICY_MAX / 2,
    RoutePreference.SAFETY: DETOUR_RATIO_POLICY_MAX,
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


def _imputed_q(edges: list[RoadEdge], ws: WeightSet) -> float:
    """q for edges with no observed indicator: length-weighted mean q of observed edges.

    Mean imputation (Little & Rubin, Statistical Analysis with Missing Data) keeps
    data-less edges from looking safest (q=0) without inventing a fixed constant.
    If nothing is observed anywhere, q is irrelevant (every edge equal), so 0.
    """
    observed = [
        (c.adverse_score, e.length_m)
        for e in edges
        if (c := compute_edge_cost(e, ws)).adverse_score is not None
    ]
    return _lw_mean(observed) or 0.0


def _q(edge: RoadEdge, ws: WeightSet, imputed: float) -> tuple[float, float]:
    c = compute_edge_cost(edge, ws)
    return (imputed if c.adverse_score is None else c.adverse_score), c.coverage


def _search_graph(edges: list[RoadEdge], ws: WeightSet, imputed: float) -> nx.DiGraph:
    """DiGraph for simple-path enumeration. Parallel edges get a virtual midpoint node
    so every edge stays a distinct candidate."""
    d = nx.DiGraph()
    for e in sorted(edges, key=lambda e: e.edge_id):
        cost = e.length_m * (1 + ws.penalty_lambda * _q(e, ws, imputed)[0])
        u, v = e.source_node, e.target_node
        if not d.has_edge(u, v):
            d.add_edge(u, v, cost=cost, edge=e)
        else:
            mid = ("virtual", e.edge_id)
            d.add_edge(u, mid, cost=cost, edge=e)
            d.add_edge(mid, v, cost=0.0, edge=None)
    return d


def _path_edges(d: nx.DiGraph, nodes: list) -> list[RoadEdge]:
    return [d[u][v]["edge"] for u, v in pairwise(nodes) if d[u][v]["edge"] is not None]


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

    allowed_edges = [graph.edge(i) for i in sorted(allowed)]
    imputed = _imputed_q(allowed_edges, ws)
    d = _search_graph(allowed_edges, ws, imputed)
    chosen = None
    for nodes in islice(
        nx.shortest_simple_paths(d, req.origin, req.destination, "cost"), MAX_CANDIDATES
    ):
        edges = _path_edges(d, nodes)
        if sum(e.length_m for e in edges) <= max_len + 1e-9:
            chosen = edges
            break
    caveats = []
    if chosen is None:
        chosen = [graph.edge(i) for i in baseline.edge_ids]
        caveats.append("허용한 우회 범위 안에서 더 나은 경로를 찾지 못해 최단 경로를 안내합니다.")
    unknown = evaluation.unknown_edge_ids
    speed = SPEED_M_PER_MIN[req.mobility_profile]
    return _result(chosen, ws, imputed, speed, baseline.length_m, caveats, unknown)


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


def _result(  # noqa: PLR0917 - private helper, call site is single
    edges: list[RoadEdge],
    ws: WeightSet,
    imputed: float,
    speed_m_per_min: float,
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
    breakdown["adverse_q"] = _lw_mean([(_q(e, ws, imputed)[0], e.length_m) for e in edges])
    coverage = {
        "indicator_coverage": _lw_mean([(_q(e, ws, imputed)[1], e.length_m) for e in edges]) or 0.0,
        **{n: sum(e.length_m for e in edges if getattr(e, n) is not None) / length for n in names},
    }
    return RouteResult(
        status=RouteStatus.UNVERIFIED if unverified else RouteStatus.OK,
        route_edge_ids=tuple(e.edge_id for e in edges),
        unverified_edge_ids=unverified,
        length_m=length,
        estimated_minutes=round(length / speed_m_per_min, 1),
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
