"""LLM 중심 루프(도구 호출·관찰·재계획). LLM은 시험용 각본(MockChatLLM)이고, Harness 동작을 본다."""

import json

from jeju_lifepath.agent.llm import MockChatLLM
from jeju_lifepath.agent.orchestrator import Agent
from tests.backend.fixtures.agent_world import NOW, PLACES
from tests.backend.unit.test_agent import LOC, fast_hash, say, tools_called, world  # noqa: F401


def call(name, **args):
    return {"content": "", "tool_calls": [{"id": f"c-{name}", "type": "function",
                                            "function": {"name": name, "arguments": json.dumps(args)}}]}


def script(*steps):
    """steps를 차례로 답한다. 받은 대화(관찰 포함)는 seen에 남긴다."""
    seen, it = [], iter(steps)

    def step(messages):
        seen.append(json.loads(json.dumps(messages)))
        return next(it, {"content": ""})

    return MockChatLLM(step), seen


def agent_with(world, llm):
    return Agent(world["store"], world["ctx"], llm=llm, clock=lambda: NOW, sleep=lambda _s: None)


def test_llm_picks_tool_and_kakao_names_never_reach_llm(world):
    llm, seen = script(call("find_nearby", facility="pharmacy", use_current_location=True),
                       {"content": "가까운 곳부터 놓았단다."})
    agent = agent_with(world, llm)
    r = say(world, "근처 약국", loc=LOC, agent=agent)
    assert r["status"] == "ok" and r["places"] and tools_called(agent) == ["find_nearby_facilities"]
    assert r["message"].endswith("가까운 곳부터 놓았단다.")
    observed = json.dumps(seen[-1], ensure_ascii=False)
    for _id, name, *_ in PLACES:  # 카카오 장소 이름은 관찰에 없다(개수만)
        assert name not in observed
    assert 'places_count' in observed


def test_knowledge_graph_then_plan_with_chosen_places(world):
    llm, seen = script(
        call("knowledge_graph", name_contains=["공원"], disabled_toilet=True),
        call("plan_trip", location_text="격자동", round_trip=True, stop_count=1, mobility="WHEELCHAIR",
             themes=["무장애 여행지"], barrier_free_names=["격자공원"]),
        {"content": ""},
    )
    agent = agent_with(world, llm)
    r = say(world, "휠체어로 격자동에서 장애인 화장실 있는 공원 한 곳 들르고 돌아오는 코스", agent=agent)
    assert "격자공원" in json.dumps(seen[1], ensure_ascii=False)  # 그래프 결과(공공자료)는 LLM이 봄
    assert r["itinerary"]["stops"][0]["name"] == "격자공원"
    assert "knowledge_graph" in [t["name"] for t in agent.last_trajectory]


def test_bad_call_returns_structured_failure_and_llm_recovers(world):
    llm, seen = script(call("teleport", to="moon"), call("find_nearby", facility="toilet", use_current_location=True))
    agent = agent_with(world, llm)
    r = say(world, "근처 화장실", loc=LOC, agent=agent)
    assert 'invalid_call' in json.dumps(seen[1], ensure_ascii=False)
    assert r["status"] == "ok" and tools_called(agent) == ["find_nearby_facilities"]


def test_ask_user_becomes_question_with_buttons(world):
    llm, _ = script(call("ask_user", question="어느 동네에서 출발하니?", options=["격자동", "격자마을"]))
    r = say(world, "코스 짜줘", agent=agent_with(world, llm))
    assert r["status"] == "needs_input" and r["message"] == "어느 동네에서 출발하니?"
    assert [o["label"] for o in r["questions"][0]["options"]] == ["격자동", "격자마을"]


def test_harness_still_confirms_names_not_in_talk(world):
    llm, _ = script(call("describe_place", place_query="서울역"))
    r = say(world, "그거 설명해줘", agent=agent_with(world, llm))
    assert r["message"] == "서울역 말하는 거니?"  # LLM이 정해도 대화에 없는 이름은 실행 전에 묻는다


def test_loop_stops_at_step_budget(world):
    llm, seen = script(*[call("find_nearby", facility="toilet", use_current_location=True)] * 20)
    agent = agent_with(world, llm)
    r = say(world, "근처 화장실", loc=LOC, agent=agent)
    assert len(seen) == world["ctx"].policy.max_loop_steps and r["status"] == "ok"


def test_button_reply_uses_existing_path(world):
    llm, seen = script(call("walking_route", location_text="격자동", destination_text="격자마을"))
    agent = agent_with(world, llm)
    r = say(world, "격자동에서 격자마을까지 걷는 길", agent=agent)
    assert r["message"] == "어떤 길로 안내해 줄까?"
    n = len(seen)
    r = say(world, "최단 경로로", agent=agent, conv=r["conversation_id"])  # 버튼 답: LLM 없이 이어감
    assert len(seen) == n and r["status"] == "ok" and r["routes"]


def test_llm_cannot_save_without_user_asking(world):
    llm, _ = script(call("save_trip", save_mode="overwrite"))
    r = say(world, "좋네", agent=agent_with(world, llm))
    assert r["message"] == "이 일정을 내 여행에 저장할까?"  # LLM 판단만으로 저장·덮어쓰지 않음
    assert r["questions"][0]["options"][0]["reply"] == "내 여행에 저장해줘"


def test_kg_distance_uses_current_location_and_is_budgeted(world):
    llm, seen = script(call("knowledge_graph", name_contains=["공원"], within_km=1), {"content": ""})
    agent = agent_with(world, llm)
    r = say(world, "가까운 공원 중 휠체어로 갈 만한 곳", loc=LOC, agent=agent)
    assert tools_called(agent) == ["knowledge_graph"]  # 도구 실행기를 거쳐 예산·기록에 들어감
    assert r["places"][0]["name"] == "격자공원" and "약 " in r["places"][0]["detail"]


def test_kg_location_name_not_in_talk_is_confirmed(world):
    llm, _ = script(call("knowledge_graph", name_contains=["공원"], location_text="서울역"))
    r = say(world, "공원 추천", agent=agent_with(world, llm))
    assert r["message"] == "서울역 말하는 거니?"


def test_missing_info_goes_back_to_llm_first(world):
    # 사용자는 "한 곳"이라고 했는데 LLM이 빠뜨림 -> 관찰로 돌려받고 채워 다시 부름(사용자에게 되묻지 않음)
    plan = dict(location_text="격자동", round_trip=True, themes=["빵집"])
    llm, seen = script(call("plan_trip", **plan), call("plan_trip", **plan, stop_count=1), {"content": ""})
    agent = agent_with(world, llm)
    r = say(world, "격자동에서 빵집 한 곳 들르고 돌아오는 코스", agent=agent)
    assert "needs_more_info" in json.dumps(seen[1], ensure_ascii=False)
    assert r["status"] == "ok" and len(r["itinerary"]["stops"]) == 1


def test_missing_info_user_question_keeps_buttons(world):
    # 사용자가 정말 말하지 않았으면 LLM이 묻기로 하고, 질문은 도구가 만든 원래 질문(버튼 포함)
    llm, _ = script(call("plan_trip", location_text="격자동", themes=["빵집"]),
                    call("ask_user", question="몇 곳 들를래?"))
    r = say(world, "격자동에서 빵집 코스", agent=agent_with(world, llm))
    assert r["status"] == "needs_input"
    assert "출발한 곳으로 돌아오기" in json.dumps(r["questions"], ensure_ascii=False)


def test_cannot_do_is_consistent_refusal_with_walking_hint(world):
    llm, _ = script(call("cannot_do", unsupported_features=["택시 경로"], transport="taxi"), {"content": ""})
    agent = agent_with(world, llm)
    r = say(world, "택시로 격자마을 가는 길", agent=agent)
    assert r["status"] == "unsupported" and tools_called(agent) == []
    assert r["questions"][0]["options"][0]["label"] == "걸어서 갈게"  # 대안은 버튼으로 제안만


def test_unresolved_question_wins_over_earlier_screen(world):
    llm, _ = script(call("knowledge_graph", name_contains=["공원"]),
                    call("plan_trip", location_text="격자동", themes=["무장애 여행지"], barrier_free_names=["격자공원"]),
                    {"content": "다 됐단다"})
    r = say(world, "격자동에서 공원 코스", agent=agent_with(world, llm))
    assert r["status"] == "needs_input"  # 일정에 빠진 정보(개수·돌아오기)를 앞 검색 화면으로 덮지 않음


def test_kg_location_question_answer_goes_back_to_llm(world):
    llm, _ = script(call("knowledge_graph", name_contains=["공원"], location_text="서울역"))
    r = say(world, "공원 추천", agent=agent_with(world, llm))
    assert r["message"] == "서울역 말하는 거니?"
    llm2, seen = script({"content": "알겠단다"})
    say(world, "응, 맞아", agent=agent_with(world, llm2), conv=r["conversation_id"])
    assert seen  # 버튼 답도 루프(LLM)로 가서 원래 조건으로 다시 찾게 한다


def test_plain_text_end_is_nudged_once_to_explicit_action(world):
    llm, seen = script({"content": "택시 길은 못 알려준단다"},
                       call("cannot_do", unsupported_features=["택시 경로"], transport="taxi"), {"content": ""})
    r = say(world, "택시로 격자마을", agent=agent_with(world, llm))
    assert r["status"] == "unsupported" and "도우미 규칙" in json.dumps(seen[1], ensure_ascii=False)


def test_reply_tool_is_a_plain_answer(world):
    llm, seen = script(call("reply", text="반갑단다. 걷는 길을 도와줄게."))
    r = say(world, "안녕", agent=agent_with(world, llm))
    assert r["message"] == "반갑단다. 걷는 길을 도와줄게." and len(seen) == 1


def test_refusal_is_kept_when_a_missing_question_follows(world):
    # 걷는 길을 먼저 시도(출발지 없음) -> 택시 거절 -> 사용자에게 묻기: 거절 문장이 질문과 함께 남아야 함
    llm, _ = script(call("walking_route", destination_text="격자마을"),
                    call("cannot_do", unsupported_features=["택시 경로"], transport="taxi"),
                    call("ask_user", question="어디서 출발하니?"))
    r = say(world, "택시 타고 격자마을 가는 길", agent=agent_with(world, llm))
    assert r["status"] == "needs_input" and "도와주지 못한단다" in r["message"]
