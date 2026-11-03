"""Cost-foundation tests. All weights here are TEST_ONLY fixtures, not evidence-based."""

import pytest
from pydantic import ValidationError

from jeju_lifepath.routing.cost import (
    IndicatorDirection,
    WeightBasis,
    WeightSet,
    compute_edge_cost,
    compute_edge_costs,
)
from tests.backend.fixtures.synthetic.grid import build_graph, make_edge

ADV = IndicatorDirection.ADVERSE
FAV = IndicatorDirection.FAVOURABLE


def _ws(**over):
    base = {
        "version": "test-0",
        "basis": WeightBasis.TEST_ONLY,
        "penalty_lambda": 1.0,
        "weights": {"cctv_index": 1.0, "traffic_exposure": 1.0},
        "directions": {"cctv_index": FAV, "traffic_exposure": ADV},
        "provenance": ["tests/backend/unit/test_cost.py"],
    }
    base.update(over)
    return WeightSet(**base)


def test_formula_full_coverage():
    edge = make_edge("A-B", "A", "B", cctv_index=0.8, traffic_exposure=0.4)
    c = compute_edge_cost(edge, _ws())
    # r_cctv = 1-0.8 = 0.2 ; r_traffic = 0.4 ; q = 0.3
    assert c.adverse_score == pytest.approx(0.3)
    assert c.cost == pytest.approx(edge.length_m * 1.3)
    assert c.coverage == 1.0
    assert c.missing_indicators == ()


def test_missing_indicator_is_renormalised_not_zero():
    edge = make_edge("A-B", "A", "B", traffic_exposure=0.4)
    c = compute_edge_cost(edge, _ws())
    assert c.adverse_score == pytest.approx(0.4)  # not (0.4 + 0)/2
    assert c.coverage == pytest.approx(0.5)
    assert c.missing_indicators == ("cctv_index",)


def test_no_observed_indicator_reports_zero_coverage():
    edge = make_edge("A-B", "A", "B")
    c = compute_edge_cost(edge, _ws())
    assert c.adverse_score is None
    assert c.coverage == 0.0
    assert c.cost == pytest.approx(edge.length_m)


def test_direction_is_owned_by_weight_set():
    edge = make_edge("A-B", "A", "B", activity_index=0.9)
    isolation = _ws(weights={"activity_index": 1.0}, directions={"activity_index": FAV})
    crowding = _ws(weights={"activity_index": 1.0}, directions={"activity_index": ADV})
    assert compute_edge_cost(edge, isolation).adverse_score == pytest.approx(0.1)
    assert compute_edge_cost(edge, crowding).adverse_score == pytest.approx(0.9)


def test_lambda_zero_is_pure_length():
    edge = make_edge("A-B", "A", "B", traffic_exposure=1.0)
    c = compute_edge_cost(edge, _ws(penalty_lambda=0.0))
    assert c.cost == pytest.approx(edge.length_m)


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_cost_is_nonnegative_and_bounded(value):
    ws = _ws(penalty_lambda=3.0)
    edge = make_edge("A-B", "A", "B", cctv_index=value, traffic_exposure=value)
    c = compute_edge_cost(edge, ws)
    assert edge.length_m <= c.cost <= edge.length_m * (1 + ws.penalty_lambda)


def test_costs_for_whole_graph():
    g = build_graph()
    costs = compute_edge_costs(list(g.edges()), _ws())
    assert set(costs) == g.edge_ids


@pytest.mark.parametrize(
    "over",
    [
        {"weights": {"crime_probability": 1.0}, "directions": {}},
        {"weights": {"cctv_index": 1.0}, "directions": {}},
        {"weights": {"cctv_index": -1.0}, "directions": {"cctv_index": FAV}},
        {"weights": {"cctv_index": 0.0}, "directions": {"cctv_index": FAV}},
        {"penalty_lambda": -0.1},
        {"provenance": []},
        {"version": " "},
    ],
)
def test_invalid_weight_sets_rejected(over):
    with pytest.raises(ValidationError):
        _ws(**over)
