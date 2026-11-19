"""CA-MOR optimizer + route validator on SYNTHETIC graphs (DEMO only)."""

import pytest

from jeju_lifepath.geo.graph import PedestrianGraph
from jeju_lifepath.routing.optimizer import SPEED_M_PER_MIN, compare_routes, find_route
from jeju_lifepath.schemas import (
    GeoPoint,
    Passability,
    RoutePreference,
    RouteRequest,
    RouteResult,
    RouteStatus,
)
from jeju_lifepath.validation.route import validate_route
from tests.backend.fixtures.synthetic.grid import SYNTHETIC_SOURCE_ID, build_graph, make_edge
from tests.backend.fixtures.user_text import assert_user_text

UNKNOWN, BLOCK = Passability.UNKNOWN, Passability.BLOCK


def req(o="A", d="C", pref="SHORTEST", detour_ratio=0.2, **kw):
    return RouteRequest(
        origin=o, destination=d, route_preference=pref, detour_ratio=detour_ratio, **kw
    )


@pytest.fixture
def two_way():
    """S-X-T: 200 m, no data / S-Y-T: 220 m, CCTV high / S-Z-T: 300 m, CCTV none.

    Observed mean q = 300/520, so the data-less S-X-T is imputed with that, not 0.
    """
    nodes = {
        "S": (126.530, 33.5),
        "X": (126.531, 33.5),
        "T": (126.532, 33.5),
        "Y": (126.531, 33.5005),
        "Z": (126.531, 33.4995),
    }

    def e(eid, u, v, length, **kw):
        return make_edge(
            eid,
            "A",
            "B",
            source_node=u,
            target_node=v,
            geometry=[nodes[u], nodes[v]],
            length_m=length,
            **kw,
        )

    return PedestrianGraph(
        [
            e("sx", "S", "X", 100),
            e("xt", "X", "T", 100),
            e("sy", "S", "Y", 110, cctv_index=1.0),
            e("yt", "Y", "T", 110, cctv_index=1.0),
            e("sz", "S", "Z", 150, cctv_index=0.0),
            e("zt", "Z", "T", 150, cctv_index=0.0),
        ]
    )


def test_closed_segment_never_used():
    g = build_graph({"BC": {"closure_status": "CLOSED"}})
    for pref in RoutePreference:
        r = find_route(g, req(pref=pref, detour_ratio=0.5))
        assert "B-C" not in r.route_edge_ids
        assert r.route_edge_ids == ("A-B", "B-E", "E-F", "F-C")
        assert validate_route(g, req(pref=pref, detour_ratio=0.5), r) == []


def test_unknown_closure_marks_unverified():
    g = build_graph({"BC": {"closure_status": "UNKNOWN"}})
    r = find_route(g, req())
    assert r.route_edge_ids == ("A-B", "B-C")
    assert r.status is RouteStatus.UNVERIFIED
    assert r.unverified_edge_ids == ("B-C",)


def test_wheelchair_unverified_route_is_flagged():
    # 휠체어: 확인 안 된 길도 쓰되(사용자 결정 2026-11-19) 결과에 확인 안 된 비율을 알린다
    g = build_graph({"BC": {"accessibility": UNKNOWN}})
    r = find_route(g, req(mobility_profile="WHEELCHAIR", detour_ratio=0.5))
    assert r.route_edge_ids == ("A-B", "B-C") and r.status is RouteStatus.UNVERIFIED
    assert any("휠체어·유모차로 지나갈 수 있는지 확인되지 않은 길이 전체의 약 50%" in c for c in r.caveats)
    # 확인된 길만 요구하면 예전처럼 PASS 구간만
    r = find_route(g, req(mobility_profile="WHEELCHAIR", detour_ratio=0.5, require_verified_accessibility=True))
    assert r.route_edge_ids == ("A-B", "B-E", "E-F", "F-C") and r.status is RouteStatus.OK


def test_wheelchair_never_uses_confirmed_barrier():
    g = build_graph({"BC": {"accessibility": BLOCK}})
    r = find_route(g, req(mobility_profile="WHEELCHAIR", detour_ratio=0.5))
    assert "B-C" not in r.route_edge_ids


def test_stairs_do_not_block_standard_pedestrian():
    g = build_graph({"BC": {"accessibility": BLOCK}})
    assert find_route(g, req()).route_edge_ids == ("A-B", "B-C")
    assert "B-C" not in find_route(g, req(avoid_stairs=True, detour_ratio=0.5)).route_edge_ids


def test_no_verified_route():
    g = build_graph({"AB": {"accessibility": UNKNOWN}, "AD": {"accessibility": UNKNOWN}})
    r = find_route(g, req(mobility_profile="WHEELCHAIR", require_verified_accessibility=True))
    assert r.status is RouteStatus.NO_FEASIBLE_ROUTE
    assert r.route_edge_ids == ()


def test_safety_prefers_observed_good_path_over_missing_data(two_way):
    # Missing data is not "safe": S-X-T has no data, so SAFETY takes the 10% longer CCTV path.
    assert find_route(two_way, req("S", "T")).route_edge_ids == ("sx", "xt")
    r = find_route(two_way, req("S", "T", "SAFETY"))
    assert r.route_edge_ids == ("sy", "yt")
    assert validate_route(two_way, req("S", "T", "SAFETY"), r) == []


def test_detour_cap_enforced(two_way):
    r = find_route(two_way, req("S", "T", "SAFETY", detour_ratio=0.05))
    assert r.route_edge_ids == ("sx", "xt")
    assert r.length_m <= r.shortest_feasible_m * 1.05
    assert r.indicator_breakdown["adverse_q"] == pytest.approx(300 / 520)
    assert r.coverage["indicator_coverage"] == 0.0


def test_compare_routes_marks_duplicates():
    out = compare_routes(build_graph(), req())
    assert list(out) == [RoutePreference.SHORTEST, RoutePreference.BALANCED, RoutePreference.SAFETY]
    assert any("최단 경로와 같습니다" in c for c in out[RoutePreference.SAFETY].caveats)


def test_deterministic(two_way):
    assert find_route(two_way, req("S", "T", "BALANCED")) == find_route(
        two_way, req("S", "T", "BALANCED")
    )


def test_result_records_provenance_and_weights():
    r = find_route(build_graph(), req())
    assert r.provenance == (SYNTHETIC_SOURCE_ID,)
    assert r.weights_version == "provisional-v1"


def test_validator_catches_violations():
    g = build_graph({"BC": {"closure_status": "CLOSED"}, "AB": {"closure_status": "UNKNOWN"}})
    bad = RouteResult(
        status=RouteStatus.OK,
        route_edge_ids=("A-B", "B-C"),
        length_m=50,
        shortest_feasible_m=50,
    )
    issues = validate_route(g, req(), bad)
    assert any(i.startswith("CONSTRAINT: B-C") for i in issues)
    assert any(i.startswith("LENGTH") for i in issues)
    assert any(i.startswith("DETOUR") for i in issues)
    assert any(i.startswith("STATUS") for i in issues)


def test_unsupported_inputs():
    g = build_graph()
    assert find_route(g, req(o=GeoPoint(lat=33.5, lon=126.53))).status is (
        RouteStatus.DATA_UNAVAILABLE
    )
    assert find_route(g, req(o="ZZ")).status is RouteStatus.DATA_UNAVAILABLE


def all_results(two_way):
    g = build_graph({"BC": {"closure_status": "UNKNOWN"}})
    return [
        *compare_routes(g, req()).values(),
        *compare_routes(two_way, req("S", "T")).values(),
        find_route(
            build_graph({"AB": {"accessibility": UNKNOWN}, "AD": {"accessibility": UNKNOWN}}),
            req(mobility_profile="WHEELCHAIR"),
        ),
        find_route(g, req(o=GeoPoint(lat=33.5, lon=126.53))),
        find_route(g, req(o="ZZ")),
        find_route(two_way, req("S", "T", "SAFETY", detour_ratio=0.0)),
    ]


def test_caveats_have_no_internal_values(two_way):
    for r in all_results(two_way):
        for c in r.caveats:
            assert_user_text(c)


def test_parallel_edges_are_distinct_candidates():
    # Two parallel A->B edges: the shorter one must be usable even if it costs more.
    g = build_graph({"A-B": {"cctv_index": 0.0}})
    alt = make_edge("A-B-alt", "A", "B", length_m=200, cctv_index=1.0)
    g2 = PedestrianGraph([*g.edges(), alt])
    r = find_route(g2, req(pref="SAFETY", detour_ratio=0.0))
    assert r.route_edge_ids[0] == "A-B"  # longer parallel edge would break the 0% cap


def test_speed_depends_on_profile():
    g = build_graph()
    std = find_route(g, req())
    wc = find_route(g, req(mobility_profile="WHEELCHAIR"))
    assert std.estimated_minutes == round(std.length_m / SPEED_M_PER_MIN["STANDARD"], 1)
    assert wc.estimated_minutes > std.estimated_minutes


def test_internal_ids_never_in_user_text(two_way):
    graph_ids = {e.edge_id for e in two_way.edges()} | set(two_way.node_ids)
    for r in all_results(two_way):
        for c in r.caveats:
            assert not any(f" {i} " in f" {c} " for i in graph_ids), c
