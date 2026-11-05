from jeju_lifepath.presentation.route import render_comparison, render_route
from jeju_lifepath.routing.optimizer import compare_routes, find_route
from jeju_lifepath.schemas import RouteRequest

from .test_routing import _all_results, req


def test_rendered_text_has_no_internal_values(toy_graph, two_way, assert_user_text):
    for r in _all_results(toy_graph, two_way):
        assert_user_text(render_route(r))
    assert_user_text(render_comparison(compare_routes(toy_graph, req())))


def test_route_text_content(two_way):
    text = render_route(find_route(two_way, req("S", "T", "SAFETY", detour_ratio=0.2)))
    assert "경로를 찾았습니다." in text
    assert "거리 약 220m" in text
    assert "CCTV 인접도 높음" in text
    assert "시연용 합성 데이터" in text
    assert "안전을 보장하지 않습니다" in text


def test_unverified_and_failure_text(toy_graph):
    assert "확인되지 않았습니다" in render_route(find_route(toy_graph, req()))
    r = find_route(toy_graph, RouteRequest(
        origin="B", destination="D", route_preference="SHORTEST",
        require_verified_accessibility=True,
    ))
    assert "찾지 못했습니다" in render_route(r)
