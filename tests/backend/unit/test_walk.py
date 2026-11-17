import pytest
from shapely.geometry import box

from jeju_lifepath.geo.osm import WalkNetwork, build_segments, is_walkable
from jeju_lifepath.presentation.route import render_plan
from jeju_lifepath.routing.walk import (
    MSG_NOT_CONNECTED,
    MSG_TOO_CLOSE,
    AccidentZones,
    WalkRouter,
)
from jeju_lifepath.schemas import MobilityProfile, RouteStatus
from jeju_lifepath.schemas.common import RoutePreference
from tests.backend.fixtures.user_text import assert_user_text

LON, LAT = 126.5, 33.5
NODES = {  # 아래 직선길(1-2-3)과 위로 살짝 돌아가는 길(1-4-5-6-3)
    1: (LON, LAT),
    2: (LON + 0.001, LAT),
    3: (LON + 0.002, LAT),
    4: (LON, LAT + 0.0003),
    5: (LON + 0.001, LAT + 0.0003),
    6: (LON + 0.002, LAT + 0.0003),
    7: (LON + 0.05, LAT + 0.05),
    8: (LON + 0.051, LAT + 0.05),  # 따로 떨어진 길
}


def way(wid, nodes, **tags):
    return {
        "type": "way",
        "id": wid,
        "nodes": nodes,
        "tags": {"highway": "footway", **tags},
        "geometry": [{"lon": NODES[n][0], "lat": NODES[n][1]} for n in nodes],
    }


ELEMENTS = [way(10, [1, 2, 3]), way(11, [1, 4, 5, 6, 3]), way(12, [7, 8])]


@pytest.fixture
def router():
    # 아래 직선길 가운데(노드 2 근처)만 사고 다발 구역
    zone = box(LON + 0.0008, LAT - 0.0001, LON + 0.0012, LAT + 0.0001)
    return WalkRouter(WalkNetwork(build_segments(ELEMENTS)), AccidentZones([zone]))


def test_walkable_rules():
    assert is_walkable({"highway": "footway"})
    assert not is_walkable({"highway": "motorway"})
    assert not is_walkable({"highway": "residential", "access": "private"})
    assert is_walkable({"highway": "residential", "access": "private", "foot": "yes"})
    assert not is_walkable({"highway": "residential", "foot": "no"})


def test_segments_split_only_at_shared_nodes():
    segs = build_segments(ELEMENTS)
    assert sorted((s.u, s.v) for s in segs) == [("1", "3"), ("1", "3"), ("7", "8")]
    shared = build_segments([*ELEMENTS, way(13, [2, 5])])  # 2와 5가 교차점이 됨
    assert len(shared) == 6


def test_safety_route_avoids_accident_zone(router):
    plan = router.plan(NODES[1], NODES[3])
    short, safe = (
        plan.routes[RoutePreference.SHORTEST].result,
        plan.routes[RoutePreference.SAFETY].result,
    )
    assert short.status is RouteStatus.UNVERIFIED
    assert safe.status is RouteStatus.UNVERIFIED
    assert short.length_m < safe.length_m <= short.length_m * 1.5
    assert short.indicator_breakdown["traffic_exposure"] > 0
    assert safe.indicator_breakdown["traffic_exposure"] == 0
    line = plan.routes[RoutePreference.SAFETY].line
    assert line[0] == pytest.approx((LAT, LON))
    assert line[-1] == pytest.approx((LAT, LON + 0.002))


def test_snap_in_middle_of_segment_makes_new_node(router):
    plan = router.plan((LON + 0.0005, LAT + 0.00001), NODES[3])
    res = plan.routes[RoutePreference.SHORTEST]
    assert res.result.length_m == pytest.approx(0.0015 * 92_900, rel=0.02)  # 경도 0.0015도 ≈ 139m
    assert res.line[0] == pytest.approx((LAT, LON + 0.0005), abs=1e-6)
    assert plan.origin_snap_m == pytest.approx(1.1, abs=0.2)


def test_both_points_on_same_segment(router):
    plan = router.plan((LON + 0.0003, LAT), (LON + 0.0013, LAT))
    res = plan.routes[RoutePreference.SHORTEST].result
    assert res.length_m == pytest.approx(0.001 * 92_900, rel=0.02)


def test_unconnected_and_identical_points(router):
    far = router.plan(NODES[1], NODES[7])
    assert far.routes[RoutePreference.SHORTEST].result.caveats == (MSG_NOT_CONNECTED,)
    same = router.plan(NODES[1], (LON + 0.000001, LAT))
    assert same.routes[RoutePreference.SHORTEST].result.caveats == (MSG_TOO_CLOSE,)


def test_render_plan_is_user_text_only(router):
    out = render_plan(router.plan(NODES[1], NODES[3]))
    texts = [*out["comparison"], *out["notes"]] + [
        t for r in out["routes"] for t in (r["title"], *r["text"])
    ]
    for t in texts:
        assert_user_text(t)
    assert "osm" not in " ".join(texts)
    assert out["comparison"] == ["사고 다발 구역을 지나는 비율은 최단 경로가 가장 높습니다."]
    assert [r["kind"] for r in out["routes"]] == ["SHORTEST", "BALANCED", "SAFETY"]


def test_same_interior_point_is_too_close(router):
    p = (LON + 0.0005, LAT)
    plan = router.plan(p, (LON + 0.0005, LAT + 0.00002))  # 같은 지점으로 붙는 두 좌표
    assert plan.routes[RoutePreference.SHORTEST].result.caveats == (MSG_TOO_CLOSE,)


def test_closed_loop_way_is_kept():
    NODES.update(
        {20: (LON, LAT - 0.001), 21: (LON + 0.001, LAT - 0.001), 22: (LON + 0.0005, LAT - 0.0015)}
    )
    loop = way(30, [20, 21, 22, 20])
    segs = build_segments([loop])
    assert len(segs) == 2
    assert {segs[0].u, segs[-1].v} == {"20"}


def test_snap_on_bent_segment_uses_metres():
    # 동쪽 100m 남짓 간 뒤 북쪽으로 꺾이는 길: 미터 기준 가운데는 꺾이는 모서리 근처
    NODES.update(
        {
            40: (LON + 0.1, LAT),
            41: (LON + 0.1 + 100 / 92_900, LAT),
            42: (LON + 0.1 + 100 / 92_900, LAT + 100 / 110_900),
            43: (LON + 0.09, LAT),
        }
    )
    net = WalkNetwork(build_segments([way(50, [40, 41, 42]), way(51, [40, 43])]))
    r = WalkRouter(net, None)
    corner = NODES[41]
    plan = r.plan((corner[0] + 0.0002, corner[1] - 0.0002), NODES[40])  # 모서리 바로 바깥
    line = plan.routes[RoutePreference.SHORTEST].line
    assert line[0] == pytest.approx((corner[1], corner[0]), abs=2e-6)


def test_stroller_avoids_confirmed_stairs():
    NODES.update(
        {
            60: (LON + 0.3, LAT),
            61: (LON + 0.301, LAT),
            62: (LON + 0.3, LAT + 0.0003),
            63: (LON + 0.301, LAT + 0.0003),
        }
    )
    elements = [
        way(70, [60, 61], highway="steps"),
        way(71, [60, 62]),
        way(72, [62, 63]),
        way(73, [63, 61]),
    ]
    r = WalkRouter(WalkNetwork(build_segments(elements)), None)
    walk = r.plan(NODES[60], NODES[61]).routes[RoutePreference.SHORTEST].result
    stroller = (
        r.plan(NODES[60], NODES[61], MobilityProfile.STROLLER)
        .routes[RoutePreference.SHORTEST]
        .result
    )
    assert walk.length_m == pytest.approx(0.001 * 92_900, rel=0.02)  # 일반 보행은 계단으로 바로
    assert stroller.length_m > walk.length_m  # 유모차는 계단을 돌아감
    assert stroller.status is RouteStatus.UNVERIFIED
