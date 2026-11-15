"""Re-validate a RouteResult against the graph (REQUIREMENTS §4.7).

Returns internal developer-facing issue strings (may contain ids); never shown to users.
"""

from __future__ import annotations

from jeju_lifepath.geo.graph import PedestrianGraph
from jeju_lifepath.routing.constraints import ConstraintEngine, ConstraintProfile
from jeju_lifepath.schemas import Passability, RouteRequest, RouteResult, RouteStatus

LENGTH_TOLERANCE_M = 1e-6


def validate_route(
    graph: PedestrianGraph,
    req: RouteRequest,
    res: RouteResult,
    engine: ConstraintEngine | None = None,
) -> list[str]:
    if not res.route_edge_ids:
        return []
    issues = [f"{i.code}: {i.detail}" for i in graph.validate_path(res.route_edge_ids)]
    if any(i.startswith("UNKNOWN_EDGE") for i in issues):
        return issues
    edges = [graph.edge(i) for i in res.route_edge_ids]
    if edges[0].source_node != req.origin or edges[-1].target_node != req.destination:
        issues.append("ENDPOINTS: origin/destination mismatch")

    evaluation = (engine or ConstraintEngine()).evaluate(
        graph.edges(), ConstraintProfile.from_request(req)
    )
    allowed = evaluation.searchable_edge_ids
    issues += [f"CONSTRAINT: {e.edge_id} not searchable" for e in edges if e.edge_id not in allowed]

    length = sum(e.length_m for e in edges)
    if abs(length - (res.length_m or 0)) > LENGTH_TOLERANCE_M:
        issues.append("LENGTH: declared length differs from edge sum")
    if res.shortest_feasible_m is not None and (
        length > res.shortest_feasible_m * (1 + req.detour_ratio) + 1e-9
    ):
        issues.append("DETOUR: exceeds detour bound")
    has_unknown = any(evaluation.verdict(e.edge_id) is Passability.UNKNOWN for e in edges)
    if res.status is RouteStatus.OK and has_unknown:
        issues.append("STATUS: OK (verified) despite UNKNOWN edges")
    return issues
