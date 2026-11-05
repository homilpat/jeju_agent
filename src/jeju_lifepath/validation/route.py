"""경로 결과 재검증: 연결성, 제약 위반, 길이상한, 검증상태 표시."""

from itertools import pairwise

import networkx as nx

from jeju_lifepath.routing.constraints import is_allowed, is_unverified
from jeju_lifepath.schemas import RouteRequest, RouteResult, RouteStatus


def validate_route(graph: nx.MultiDiGraph, req: RouteRequest, res: RouteResult) -> list[str]:
    if not res.route_edge_ids:
        return []
    by_id = {k: e for _, _, k, e in graph.edges(keys=True, data="edge")}
    missing = [eid for eid in res.route_edge_ids if eid not in by_id]
    if missing:
        return [f"알 수 없는 구간: {eid}" for eid in missing]
    edges = [by_id[eid] for eid in res.route_edge_ids]

    issues: list[str] = []
    if edges[0].source_node != req.origin or edges[-1].target_node != req.destination:
        issues.append("출발/도착 불일치")
    for a, b in pairwise(edges):
        if a.target_node != b.source_node:
            issues.append(f"단절: {a.edge_id} -> {b.edge_id}")
    issues += [f"제약 위반: {e.edge_id}" for e in edges if not is_allowed(e, req)]
    length = sum(e.length_m for e in edges)
    if abs(length - (res.length_m or 0)) > 1e-6:
        issues.append("길이 불일치")
    base = res.indicator_breakdown.get("shortest_feasible_m")
    if base is not None and length > base * (1 + req.detour_ratio) + 1e-9:
        issues.append("우회율 상한 위반")
    if res.status is RouteStatus.OK and any(is_unverified(e) for e in edges):
        issues.append("미확인 구간 포함인데 OK(검증 완료) 표시")
    return issues
