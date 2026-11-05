import networkx as nx
import pytest

from jeju_lifepath.geo.graph import build_graph


def test_toy_graph_loads(toy_graph):
    assert toy_graph.number_of_edges() == 7
    keys = [k for *_, k in toy_graph.edges(keys=True)]
    assert len(keys) == len(set(keys))


def test_duplicate_edge_id_rejected(toy_edges):
    with pytest.raises(ValueError, match="duplicate"):
        build_graph(toy_edges + [toy_edges[0]])


def test_baseline_shortest_path(toy_graph):
    # 제약 미적용 기준 최단경로 A-B-D (200m). BLOCK 제외는 G5/G6에서 검증.
    path = nx.shortest_path(toy_graph, "A", "D", weight="length_m")
    assert path == ["A", "B", "D"]
