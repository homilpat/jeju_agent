import pytest
from pydantic import ValidationError

from jeju_lifepath.schemas import Accessibility, ClosureStatus, RoadEdge, RouteRequest

BASE = dict(
    edge_id="e1",
    source_node="a",
    target_node="b",
    geometry=[(126.5, 33.5), (126.51, 33.5)],
    length_m=10,
    source_ids=["DEMO"],
)


def test_defaults_are_unknown_not_safe():
    e = RoadEdge(**BASE)
    assert e.accessibility is Accessibility.UNKNOWN
    assert e.closure_status is ClosureStatus.UNKNOWN
    assert e.cctv_index is None


@pytest.mark.parametrize(
    "override",
    [
        {"length_m": 0},
        {"cctv_index": 1.2},
        {"lighting_index": -0.1},
        {"source_ids": []},
        {"geometry": [(126.5, 33.5)]},
        {"accessibility": "MAYBE"},
        {"unexpected": 1},
    ],
)
def test_edge_rejects_invalid(override):
    with pytest.raises(ValidationError):
        RoadEdge(**{**BASE, **override})


def test_request_detour_ratio_capped():
    with pytest.raises(ValidationError):
        RouteRequest(origin="A", destination="D", route_preference="SAFETY", detour_ratio=0.6)
    req = RouteRequest(origin="A", destination="D", route_preference="SHORTEST")
    assert req.detour_ratio == 0.2
