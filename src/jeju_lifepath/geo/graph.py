"""RoadEdge 목록 → 단일 방향성 보행 그래프. edge key = edge_id."""

import json
from pathlib import Path

import networkx as nx

from jeju_lifepath.schemas import RoadEdge


def build_graph(edges: list[RoadEdge]) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    seen: set[str] = set()
    for e in edges:
        if e.edge_id in seen:
            raise ValueError(f"duplicate edge_id: {e.edge_id}")
        seen.add(e.edge_id)
        g.add_edge(e.source_node, e.target_node, key=e.edge_id, length_m=e.length_m, edge=e)
    return g


def load_edges(path: str | Path) -> list[RoadEdge]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [RoadEdge.model_validate(d) for d in data["edges"]]
