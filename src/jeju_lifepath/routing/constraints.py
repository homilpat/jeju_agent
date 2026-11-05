"""Hard constraint 필터 (B01). BLOCK·CLOSED는 항상 제외, UNKNOWN은 남기되 표시."""

import networkx as nx

from jeju_lifepath.schemas import Accessibility, ClosureStatus, RoadEdge, RouteRequest


def is_allowed(e: RoadEdge, req: RouteRequest) -> bool:
    if e.accessibility is Accessibility.BLOCK or e.closure_status is ClosureStatus.CLOSED:
        return False
    if req.require_verified_accessibility and e.accessibility is not Accessibility.PASS:
        return False
    return True


def is_unverified(e: RoadEdge) -> bool:
    return e.accessibility is not Accessibility.PASS or e.closure_status is not ClosureStatus.OPEN


def filter_edges(graph: nx.MultiDiGraph, req: RouteRequest) -> nx.MultiDiGraph:
    keep = [(u, v, k) for u, v, k, e in graph.edges(keys=True, data="edge") if is_allowed(e, req)]
    return graph.edge_subgraph(keep)
