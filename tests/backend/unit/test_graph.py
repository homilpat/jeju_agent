import random

import pytest

from jeju_lifepath.geo.graph import (
    GraphIntegrityError,
    PedestrianGraph,
    UnknownEdgeError,
    UnknownNodeError,
)
from tests.backend.fixtures.synthetic_grid import (
    NODES,
    SEGMENTS,
    build_edges,
    build_graph,
    make_edge,
)


def test_graph_shape():
    g = build_graph()
    assert len(g) == 2 * len(SEGMENTS)
    assert g.node_ids == frozenset(NODES)
    assert g.node_coord("A") == NODES["A"]
    assert {e.edge_id for e in g.out_edges("B")} == {"B-A", "B-C", "B-E"}


def test_fixture_lengths_consistent_with_geometry():
    assert build_graph().length_discrepancies() == []


def test_single_strongly_connected_component():
    comps = build_graph().strongly_connected_components()
    assert len(comps) == 1
    assert comps[0] == frozenset(NODES)


def test_baseline_shortest_path():
    path = build_graph().shortest_path_by_length("A", "C")
    assert path.edge_ids == ("A-B", "B-C")
    assert path.node_ids == ("A", "B", "C")
    assert path.length_m == pytest.approx(
        sum(build_graph().edge(e).length_m for e in path.edge_ids)
    )


def test_shortest_path_is_input_order_independent():
    edges = build_edges()
    expected = PedestrianGraph(edges).shortest_path_by_length("A", "F")
    for seed in range(5):
        shuffled = edges[:]
        random.Random(seed).shuffle(shuffled)
        assert PedestrianGraph(shuffled).shortest_path_by_length("A", "F") == expected


def test_allowed_edges_restrict_search():
    g = build_graph()
    allowed = g.edge_ids - {"B-C"}
    path = g.shortest_path_by_length("A", "C", allowed)
    assert "B-C" not in path.edge_ids
    assert g.validate_path(path.edge_ids) == []


def test_directionality_respected():
    g = build_graph()
    # Only the reverse direction C-B is removed: A→C may still use B-C.
    assert g.shortest_path_by_length("A", "C", g.edge_ids - {"C-B"}).edge_ids == ("A-B", "B-C")


def test_no_path_returns_none():
    g = build_graph()
    assert g.shortest_path_by_length("A", "C", {"A-B"}) is None
    assert not g.is_reachable("A", "C", {"A-B"})


def test_parallel_edges_pick_shorter_deterministically():
    edges = [*build_edges(), make_edge("A-B#alt", "A", "B", length_m=50.0)]
    path = PedestrianGraph(edges).shortest_path_by_length("A", "C")
    assert path.edge_ids == ("A-B#alt", "B-C")


def test_unknown_references_raise():
    g = build_graph()
    with pytest.raises(UnknownNodeError):
        g.shortest_path_by_length("A", "Z")
    with pytest.raises(UnknownEdgeError):
        g.shortest_path_by_length("A", "C", {"X-Y"})
    with pytest.raises(UnknownEdgeError):
        g.edge("X-Y")
    with pytest.raises(ValueError, match="differ"):
        g.shortest_path_by_length("A", "A")


def test_empty_graph_rejected():
    with pytest.raises(GraphIntegrityError):
        PedestrianGraph([])


def test_duplicate_edge_id_rejected():
    with pytest.raises(GraphIntegrityError, match="duplicate"):
        PedestrianGraph([*build_edges(), make_edge("A-B", "A", "B")])


def test_node_coordinate_mismatch_rejected():
    bad = make_edge("A-X", "A", "B").model_copy()
    bad_edge = type(bad).model_validate(
        {**bad.model_dump(), "geometry": [(126.5400, 33.5100), NODES["B"]]}
    )
    with pytest.raises(GraphIntegrityError, match="mismatch"):
        PedestrianGraph([*build_edges(), bad_edge])


def test_length_discrepancy_detected():
    g = PedestrianGraph(build_edges({"A-B": {"length_m": 500.0}}))
    found = g.length_discrepancies()
    assert [d.edge_id for d in found] == ["A-B"]
    assert found[0].relative_error > 1.0


def test_validate_path_issues():
    g = build_graph()
    codes = lambda ids: [i.code for i in g.validate_path(ids)]  # noqa: E731
    assert codes([]) == ["EMPTY_PATH"]
    assert codes(["A-B", "X"]) == ["UNKNOWN_EDGE"]
    assert codes(["A-B", "E-F"]) == ["DISCONTINUOUS"]
    assert codes(["A-B", "B-A", "A-B"]) == ["REPEATED_EDGE"]


def test_disconnected_components_reported():
    edges = [make_edge("A-B", "A", "B"), make_edge("E-F", "E", "F")]
    g = PedestrianGraph(edges)
    assert len(g.strongly_connected_components()) == 4
    assert g.shortest_path_by_length("A", "F") is None


def test_with_edges_preserves_topology():
    g = build_graph()
    with pytest.raises(GraphIntegrityError):
        g.with_edges(list(g.edges())[:-1])
