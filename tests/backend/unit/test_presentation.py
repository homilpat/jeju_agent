from jeju_lifepath.presentation.route import START_NOTICE, render_comparison, render_route
from jeju_lifepath.routing.optimizer import compare_routes, find_route
from tests.backend.fixtures.synthetic.grid import build_graph
from tests.backend.fixtures.user_text import assert_user_text
from tests.backend.unit.test_routing import all_results, req, two_way  # noqa: F401


def test_rendered_text_has_no_internal_values(two_way):  # noqa: F811
    for r in all_results(two_way):
        assert_user_text(render_route(r))
    assert_user_text(render_comparison(compare_routes(build_graph(), req())))


def test_route_text_content(two_way):  # noqa: F811
    text = render_route(find_route(two_way, req("S", "T", "SAFETY")))
    assert "경로를 찾았습니다." in text
    assert "거리 약 220m" in text
    assert "CCTV 인접도 높음" in text
    assert "시연용 합성 데이터" in text
    assert "보장" not in text  # safety notice is shown once on the start screen only


def test_unverified_and_failure_text():
    g = build_graph({"BC": {"closure_status": "UNKNOWN"}})
    assert "확인되지 않았습니다" in render_route(find_route(g, req()))
    blocked = build_graph({"AB": {"accessibility": "UNKNOWN"}, "AD": {"accessibility": "UNKNOWN"}})
    assert "찾지 못했습니다" in render_route(
        find_route(blocked, req(mobility_profile="WHEELCHAIR"))
    )


def test_start_notice_is_plain():
    assert_user_text(START_NOTICE)
    assert "대리변수" not in START_NOTICE
