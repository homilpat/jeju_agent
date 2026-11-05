from pathlib import Path

import pytest

from jeju_lifepath.geo.graph import build_graph, load_edges

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def toy_edges():
    return load_edges(FIXTURES / "toy_graph.json")


@pytest.fixture
def toy_graph(toy_edges):
    return build_graph(toy_edges)
