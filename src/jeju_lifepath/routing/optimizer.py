"""CA-MOR 경로 탐색 (B02/B03).

길이상한 하 최소비용: 비용 오름차순으로 단순경로를 열거해 상한을 만족하는 첫 경로를 택한다.
MAX_CANDIDATES 안에서 찾으면 그 경로는 상한 제약 하 최소비용이다.
"""

from itertools import islice, pairwise

import networkx as nx

from jeju_lifepath.routing.constraints import filter_edges, is_unverified
from jeju_lifepath.routing.cost import WEIGHTS, WEIGHTS_VERSION, adverse_score, edge_cost
from jeju_lifepath.schemas import (
    RoadEdge,
    RoutePreference,
    RouteRequest,
    RouteResult,
    RouteStatus,
)

MAX_CANDIDATES = 200
WALK_M_PER_MIN = 75.0  # 4.5 km/h
SAFETY_CAVEAT = "안전환경 지표는 시설 설치 위치 기반 대리변수이며 안전을 보장하지 않습니다."


def _collapse(graph: nx.MultiDiGraph, pref: RoutePreference) -> nx.DiGraph:
    # shortcut: 평행 구간은 비용 최소 1개만 남김, 더 비싸지만 짧은 평행 구간이 중요해지면 확장
    d = nx.DiGraph()
    for u, v, e in graph.edges(data="edge"):
        c = edge_cost(e, pref)
        if not d.has_edge(u, v) or c < d[u][v]["cost"]:
            d.add_edge(u, v, cost=c, length=e.length_m, edge=e)
    return d


def _path_edges(d: nx.DiGraph, nodes: list[str]) -> list[RoadEdge]:
    return [d[u][v]["edge"] for u, v in pairwise(nodes)]


def find_route(graph: nx.MultiDiGraph, req: RouteRequest) -> RouteResult:
    if not isinstance(req.origin, str) or not isinstance(req.destination, str):
        return RouteResult(
            status=RouteStatus.DATA_UNAVAILABLE, caveats=["좌표 스냅 미구현(G2 예정)"]
        )
    valid = filter_edges(graph, req)
    if req.origin not in valid or req.destination not in valid:
        return _no_route(req)

    shortest = _collapse(valid, RoutePreference.SHORTEST)
    try:
        base_len = nx.shortest_path_length(shortest, req.origin, req.destination, "length")
    except nx.NetworkXNoPath:
        return _no_route(req)
    max_len = base_len * (1 + req.detour_ratio)

    d = _collapse(valid, req.route_preference)
    caveats: list[str] = []
    chosen = None
    paths = nx.shortest_simple_paths(d, req.origin, req.destination, "cost")
    for nodes in islice(paths, MAX_CANDIDATES):
        edges = _path_edges(d, nodes)
        if sum(e.length_m for e in edges) <= max_len + 1e-9:
            chosen = edges
            break
    if chosen is None:
        nodes = nx.shortest_path(shortest, req.origin, req.destination, "length")
        chosen = _path_edges(shortest, nodes)
        caveats.append(f"후보 {MAX_CANDIDATES}개 내 상한 만족 경로 없음: 최단경로로 대체")
    return _result(chosen, req, base_len, caveats)


def _no_route(req: RouteRequest) -> RouteResult:
    msg = (
        "검증된(PASS) 구간만으로 연결되는 경로가 없습니다."
        if req.require_verified_accessibility
        else "통행 가능한 경로가 없습니다."
    )
    return RouteResult(status=RouteStatus.NO_FEASIBLE_ROUTE, caveats=[msg])


def _lw_mean(values: list[tuple[float, float]]) -> float | None:
    """(값, 길이) 목록의 길이가중 평균."""
    w = sum(lm for _, lm in values)
    return sum(v * lm for v, lm in values) / w if w else None


def _result(
    edges: list[RoadEdge], req: RouteRequest, base_len: float, caveats: list[str]
) -> RouteResult:
    length = sum(e.length_m for e in edges)
    unverified = [e.edge_id for e in edges if is_unverified(e)]
    if unverified:
        caveats.append(f"접근성/폐쇄 미확인 구간 포함: {', '.join(unverified)}")
    if req.route_preference is not RoutePreference.SHORTEST:
        caveats.append(SAFETY_CAVEAT)

    breakdown = {
        name: _lw_mean(
            [(getattr(e, name), e.length_m) for e in edges if getattr(e, name) is not None]
        )
        for name in WEIGHTS
    }
    breakdown["adverse_q"] = _lw_mean([(adverse_score(e)[0], e.length_m) for e in edges])
    coverage = {
        "indicator_coverage": _lw_mean([(adverse_score(e)[1], e.length_m) for e in edges]),
        **{
            name: sum(e.length_m for e in edges if getattr(e, name) is not None) / length
            for name in WEIGHTS
        },
    }
    return RouteResult(
        status=RouteStatus.UNVERIFIED if unverified else RouteStatus.OK,
        route_edge_ids=[e.edge_id for e in edges],
        length_m=length,
        estimated_minutes=round(length / WALK_M_PER_MIN, 1),
        indicator_breakdown={
            **breakdown,
            "weights_version": WEIGHTS_VERSION,
            "shortest_feasible_m": base_len,
        },
        coverage=coverage,
        provenance=sorted({s for e in edges for s in e.source_ids}),
        caveats=caveats,
    )


def compare_routes(graph: nx.MultiDiGraph, req: RouteRequest) -> dict[str, RouteResult]:
    """최단/균형/안전환경 후보. 같은 경로면 가짜 대안을 만들지 않고 중복을 명시한다."""
    out: dict[str, RouteResult] = {}
    for pref in RoutePreference:
        r = find_route(graph, req.model_copy(update={"route_preference": pref}))
        for other_pref, other in out.items():
            if r.route_edge_ids and r.route_edge_ids == other.route_edge_ids:
                r.caveats.append(f"{other_pref} 경로와 동일")
                break
        out[pref.value] = r
    return out
