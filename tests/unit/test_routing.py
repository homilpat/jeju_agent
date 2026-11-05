import re

import pytest

from jeju_lifepath.geo.graph import build_graph
from jeju_lifepath.routing.cost import adverse_score
from jeju_lifepath.routing.optimizer import compare_routes, find_route
from jeju_lifepath.schemas import RoadEdge, RouteRequest, RouteResult, RouteStatus
from jeju_lifepath.validation.route import validate_route


def req(o="A", d="D", pref="SHORTEST", **kw):
    return RouteRequest(origin=o, destination=d, route_preference=pref, **kw)


def edge(eid, s, t, length, **kw):
    kw.setdefault("accessibility", "PASS")
    kw.setdefault("closure_status", "OPEN")
    return RoadEdge(
        edge_id=eid, source_node=s, target_node=t, geometry=[(0, 0), (1, 1)],
        length_m=length, source_ids=["DEMO"], **kw,
    )


@pytest.fixture
def two_way():
    # S-X-T: 짧고 CCTV 없음 / S-Y-T: 10% 길고 CCTV 높음
    return build_graph([
        edge("sx", "S", "X", 100, cctv_index=0.0),
        edge("xt", "X", "T", 100, cctv_index=0.0),
        edge("sy", "S", "Y", 110, cctv_index=1.0),
        edge("yt", "Y", "T", 110, cctv_index=1.0),
    ])


def test_block_and_closed_never_used(toy_graph):
    for pref in ("SHORTEST", "BALANCED", "SAFETY"):
        r = find_route(toy_graph, req(pref=pref, detour_ratio=0.5))
        assert not {"e_BD", "e_BC"} & set(r.route_edge_ids)
        assert validate_route(toy_graph, req(pref=pref, detour_ratio=0.5), r) == []


def test_unknown_edge_marks_unverified(toy_graph):
    r = find_route(toy_graph, req())
    assert r.route_edge_ids == ["e_AC", "e_CD"]
    assert r.status is RouteStatus.UNVERIFIED
    assert r.unverified_edge_ids == ["e_CD"]


def test_verified_accessibility_is_pass_only(toy_graph):
    r = find_route(toy_graph, req(require_verified_accessibility=True))
    assert r.route_edge_ids == ["e_AC", "e_CE", "e_ED"]
    # e_ED 폐쇄정보 UNKNOWN → 검증 완료(OK) 표시 금지
    assert r.status is RouteStatus.UNVERIFIED


def test_no_feasible_route(toy_graph):
    r = find_route(toy_graph, req(o="B", require_verified_accessibility=True))
    assert r.status is RouteStatus.NO_FEASIBLE_ROUTE
    assert r.route_edge_ids == []


def test_safety_prefers_better_indicators_within_cap(two_way):
    assert find_route(two_way, req("S", "T")).route_edge_ids == ["sx", "xt"]
    r = find_route(two_way, req("S", "T", "SAFETY", detour_ratio=0.2))
    assert r.route_edge_ids == ["sy", "yt"]
    assert validate_route(two_way, req("S", "T", "SAFETY", detour_ratio=0.2), r) == []


def test_detour_cap_enforced(two_way):
    r = find_route(two_way, req("S", "T", "SAFETY", detour_ratio=0.05))
    assert r.route_edge_ids == ["sx", "xt"]
    assert r.length_m <= r.indicator_breakdown["shortest_feasible_m"] * 1.05


def test_compare_routes_marks_duplicates(toy_graph):
    out = compare_routes(toy_graph, req())
    assert set(out) == {"SHORTEST", "SAFETY", "BALANCED"}
    assert any("최단 경로와 같습니다" in c for c in out["SAFETY"].caveats)


def test_deterministic(two_way):
    r1 = find_route(two_way, req("S", "T", "BALANCED"))
    r2 = find_route(two_way, req("S", "T", "BALANCED"))
    assert r1 == r2


def test_missing_indicators_not_treated_as_safe():
    q, coverage = adverse_score(edge("e", "a", "b", 10))
    assert q == 0.5 and coverage == 0


def test_validator_catches_block_and_bad_ok(toy_graph):
    bad = RouteResult(
        status=RouteStatus.OK, route_edge_ids=["e_AB", "e_BD"], length_m=200,
        indicator_breakdown={"shortest_feasible_m": 250},
    )
    issues = validate_route(toy_graph, req(), bad)
    assert "제약 위반: e_BD" in issues
    assert any("OK(검증 완료)" in i for i in issues)


def test_coordinates_not_supported_yet(toy_graph):
    r = find_route(toy_graph, req(o=(126.5, 33.5)))
    assert r.status is RouteStatus.DATA_UNAVAILABLE


def test_caveats_have_no_internal_values(toy_graph, two_way):
    # 사용자 문구에 구간 ID, enum 이름, JSON 기호가 섞이면 안 된다
    internal = re.compile(r"e_[A-Z]|\b(?:sx|xt|sy|yt)\b|[A-Z]{4,}|[{}\[\]]")
    results = [
        *compare_routes(toy_graph, req()).values(),
        *compare_routes(two_way, req("S", "T")).values(),
        find_route(toy_graph, req(o="B", require_verified_accessibility=True)),
        find_route(toy_graph, req(o=(126.5, 33.5))),
    ]
    for r in results:
        for c in r.caveats:
            assert not internal.search(c), c
