"""Agent 결과 -> 공통 응답(12.7). 사용자에게 보이는 값(message, questions, itinerary/places의 글자,
warnings, missing_data, claims의 text)은 모두 한국어 문장이다. 상태·ID 같은 내부값은 화면 코드가
분기에만 쓰고 표시하지 않는다(REQUIREMENTS §7)."""

from __future__ import annotations

from datetime import UTC, datetime

from jeju_lifepath.agent.kakao import SOURCE as KAKAO_SOURCE
from jeju_lifepath.agent.services import PUBLIC_SOURCES
from jeju_lifepath.schemas import MobilityProfile

FACILITY_WORD = {
    "pharmacy": "약국",
    "toilet": "화장실",
    "wifi": "공공 와이파이",
    "police": "경찰관서",
}
MOBILITY_WORD = {MobilityProfile.STROLLER: "유모차", MobilityProfile.WHEELCHAIR: "휠체어"}
TOOL_WORD = {
    "search_places": "장소 검색",
    "resolve_location": "위치 찾기",
    "find_nearby_facilities": "근처 시설 찾기",
    "compare_routes": "걷는 길 계산",
    "get_travel_matrix": "걷는 거리 계산",
}
HOURS_UNVERIFIED = "영업시간은 확인하지 못했어요. 가기 전에 카카오맵에서 확인해 주세요."
GREEDY_NOTE = (
    "이동이 가장 덜 늘어나는 순서로 고른 제안이에요. 가장 좋은 조합이라고 보장하지는 않아요."
)
HELP = (
    "이런 걸 도와드릴 수 있어요: 근처 약국·화장실·와이파이 찾기, 두 곳 사이 걷는 길 비교, "
    "들를 곳을 넣은 걷기 일정 만들기와 고치기. 예: '애월에서 빵집 두 곳 들르는 코스 짜줘'"
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ids(ctx) -> tuple[str, str]:
    return ctx["run_id"], ctx["conv_id"]


def base(run_id: str, conv_id: str, status: str, message: str, version: str, **kw) -> dict:
    out = {
        "request_id": run_id,
        "run_id": run_id,
        "conversation_id": conv_id,
        "trip_id": None,
        "trip_version": None,
        "status": status,
        "message": message,
        "questions": [],
        "itinerary": None,
        "routes": None,
        "places": [],
        "claims": [],
        "evidence": [],
        "missing_data": [],
        "warnings": [],
        "policy_version": version,
        "dataset_snapshots": [],
        "trace_ref": run_id,
        "interpreter": "rules",
    }
    out.update(kw)
    return out


def _from(ctx, status: str, message: str, version: str, **kw) -> dict:
    run_id, conv_id = _ids(ctx)
    parse = ctx.get("parse")
    return base(
        run_id,
        conv_id,
        status,
        message,
        version,
        interpreter=parse.parser if parse else "rules",
        **kw,
    )


def _distance(m: float | None) -> str:
    if m is None:
        return "거리 확인 못 함"
    return f"{m / 1000:.1f}km" if m >= 1000 else f"{round(m)}m"


def run_summary(trajectory: list[dict]) -> str:
    counts: dict[str, int] = {}
    for t in trajectory:
        word = TOOL_WORD.get(t["name"])
        if word:
            counts[word] = counts.get(word, 0) + 1
    if not counts:
        return "도구를 쓰지 않고 답했어요."
    return ", ".join(f"{w} {n}번" for w, n in counts.items()) + " 했어요."


KAKAO_TOOLS = frozenset({"search_places", "resolve_location", "find_nearby_facilities"})
STORED_NOTE = "장소 정보가 담긴 답은 저장하지 않아요. 결과를 다시 보려면 한 번 더 물어봐 주세요."


def used_kakao(trajectory: list[dict]) -> bool:
    return any(t["name"] in KAKAO_TOOLS for t in trajectory)


def for_storage(resp: dict, kakao: bool) -> dict:
    """저장할 응답 사본: 카카오 검색 결과(장소 이름·주소·좌표, 그 좌표로 그린 선)를 뺀다(운영정책).
    장소 ID는 남겨도 된다. 화면에 보낸 원래 응답은 그대로 둔다."""
    if not kakao:
        return resp
    out = {k: v for k, v in resp.items() if k not in ("commit", "options_state")}
    if resp["status"] != "needs_input":
        out["message"] = STORED_NOTE
    out["places"] = []
    out["questions"] = [
        {**q, "options": [{"label": f"{i + 1}번", "reply": o["reply"]}
                          for i, o in enumerate(q.get("options", []))]}
        for q in resp.get("questions", [])
    ]
    if resp.get("itinerary"):
        out["itinerary"] = {
            **resp["itinerary"],
            "stops": [
                {**s, "name": f"{s['number']}번 장소", "detail": "", "lat": None, "lon": None}
                for s in resp["itinerary"]["stops"]
            ],
        }
    if resp.get("routes"):
        out["routes"] = {**resp["routes"],
                         "routes": [{**r, "line": []} for r in resp["routes"]["routes"]]}
    return out


def stage_text(stage: str) -> str:
    return {
        "completed": "답을 마쳤어요.",
        "cancelled": "취소했어요.",
        "failed": "끝까지 처리하지 못했어요.",
        "interrupted": "처리 중에 멈췄어요. 이어서 하기를 누르면 다시 계산해요.",
    }.get(stage, "처리하고 있어요.")


# 흐름 제어 ---------------------------------------------------------------
def still_running(run_id: str, conv_id: str) -> dict:
    # still_running: 화면이 실행 상태를 다시 확인하라는 표시(사용자에게 보이지 않음)
    return base(
        run_id, conv_id, "partial", "같은 메시지를 처리하고 있어요. 잠시 후 다시 확인해 주세요.", "",
        still_running=True, summary="",
    )


def cancelled(run_id: str, conv_id: str, version: str) -> dict:
    return base(run_id, conv_id, "cancelled", "요청을 취소했어요. 일정은 바뀌지 않았어요.", version)


def needs_input(run_id, conv_id, questions, version, parse, trip_id, trip_version) -> dict:
    lead = questions[0]["text"] if len(questions) == 1 else "몇 가지만 알려 주세요."
    return base(
        run_id,
        conv_id,
        "needs_input",
        lead,
        version,
        questions=questions,
        trip_id=trip_id,
        trip_version=trip_version,
        interpreter=parse.parser if parse else "rules",
    )


def budget_stop(run_id, conv_id, kind: str, ctx, version) -> dict:
    msg = (
        "시간이 오래 걸려 여기서 멈췄어요. 일정은 바뀌지 않았어요. 조건을 줄여 다시 요청해 주세요."
        if kind == "deadline"
        else "같은 문제가 반복돼 여기서 멈췄어요. 일정은 바뀌지 않았어요. 잠시 후 다시 시도해 주세요."
    )
    return base(
        run_id, conv_id, "partial", msg, version, missing_data=["계산을 끝내지 못한 부분이 있어요."]
    )


def tool_failure(run_id, conv_id, kind: str, ctx, version) -> dict:
    msg = {
        "auth_config": "지금은 장소 검색 서비스에 연결할 수 없어요(설정 문제). 걷는 길 비교는 계속 쓸 수 있어요.",
        "transient": "검색 서비스가 잠시 응답하지 않아요. 잠시 후 다시 시도해 주세요.",
        "timeout": "검색 서비스가 너무 늦게 응답했어요. 잠시 후 다시 시도해 주세요.",
        "unavailable": "지금은 경로를 계산할 수 없어요. 길 자료가 준비되지 않았어요.",
        "invalid_args": "입력한 내용을 다시 확인해 주세요.",
    }.get(kind, "잠시 문제가 생겼어요. 다시 시도해 주세요.")
    return base(run_id, conv_id, "error", msg, version)


def version_conflict(ctx, version) -> dict:
    return _from(
        ctx,
        "error",
        "그사이 다른 곳에서 일정이 바뀌었어요. 최신 일정을 보고 다시 말씀해 주세요. 이번 변경은 저장하지 않았어요.",
        version,
    )


def unsupported_line(features: list[str]) -> str:
    return f"{'·'.join(features)}은(는) 아직 도와드리지 못해요."


def unsupported(ctx, p, version) -> dict:
    feats = p.unsupported_features or ["요청하신 기능"]
    msg = unsupported_line(feats)
    if "차량 이동" in feats:
        msg += " 걷는 길로 안내받으려면 '걸어서'라고 말해 주세요."
    return _from(ctx, "unsupported", msg, version)


def chat(ctx, p, version) -> dict:
    return _from(ctx, "ok", HELP, version)


# 안내 --------------------------------------------------------------------
def _evidence(source: str) -> list[dict]:
    return [{"source": source, "checked_at": _now()}]


def place_choices(ctx, p, items, version) -> dict:
    return {
        **_from(
            ctx,
            "needs_input",
            "같은 이름의 장소가 여러 곳이에요. 어느 곳인가요?",
            version,
            questions=[
                {
                    "text": "어느 곳을 말씀하시나요?",
                    "options": [
                        {"label": it["label"], "reply": f"{i + 1}번"} for i, it in enumerate(items)
                    ],
                }
            ],
        ),
        "options_state": {
            "slot": "place_query",
            "items": [{"ref": it["ref"], "query": it["query"]} for it in items],
            "parse": p.model_dump(mode="json"),
        },
    }


def place_info(ctx, p, places, r, version) -> dict:
    if not places:
        return _from(
            ctx,
            "ok",
            f"'{p.slots.place_query}'을(를) 찾지 못했어요. 다른 이름으로 알려 주세요.",
            version,
        )
    x = places[0]
    lines = [f"{x['name']}: {x['category'] or '분류 정보 없음'}, {x['address']}."]
    return _from(
        ctx,
        "ok",
        " ".join(lines),
        version,
        places=[
            {
                "name": x["name"],
                "detail": x["category"] or "",
                "address": x["address"],
                "url": x["url"],
                "lat": x["lat"],
                "lon": x["lon"],
            }
        ],
        claims=[
            {"text": "장소 이름·주소", "verified": True, "evidence": 0},
            {"text": "영업시간·메뉴", "verified": False, "evidence": None},
        ],
        evidence=_evidence(KAKAO_SOURCE),
        missing_data=[HOURS_UNVERIFIED],
    )


def facilities(ctx, p, at, r, version) -> dict:
    word = FACILITY_WORD[p.slots.facility]
    found = r["places"]
    source = KAKAO_SOURCE if r["source"] == "kakao" else PUBLIC_SOURCES[p.slots.facility]
    if r.get("outside"):
        return _from(ctx, "ok", "제주 안에서만 찾을 수 있어요.", version)
    if not found:  # 정상적인 0건: 오류와 다른 문구(11.3, AT-06)
        return _from(
            ctx,
            "ok",
            f"{at['label']} 근처에서 {word}을(를) 찾지 못했어요.",
            version,
            evidence=_evidence(source),
        )
    places = [
        {
            "name": x["name"],
            "detail": " · ".join(
                filter(
                    None,
                    [
                        f"약 {_distance(x.get('distance_m'))}",
                        x.get("detail") or x.get("category"),
                        *(x.get("notes") or []),
                        f"여는 시간 {x['hours']}" if x.get("hours") else None,
                    ],
                )
            ),
            "address": x.get("address", ""),
            "url": x.get("url"),
            "lat": x["lat"],
            "lon": x["lon"],
        }
        for x in found
    ]
    missing = ["거리는 직선거리예요."]
    if p.slots.facility == "pharmacy":
        missing.append(HOURS_UNVERIFIED)
    if r.get("partial"):
        missing.append("후보가 많아 일부만 살펴봤어요.")
    return _from(
        ctx,
        "ok",
        f"{at['label']}에서 가까운 {word} {len(places)}곳이에요.",
        version,
        places=places,
        evidence=_evidence(source),
        missing_data=missing,
        claims=[{"text": f"{word} 위치", "verified": True, "evidence": 0}]
        + (
            [{"text": "영업 여부", "verified": False, "evidence": None}]
            if p.slots.facility == "pharmacy"
            else []
        ),
    )


def theme_places(ctx, p, at, found, widened, version) -> dict:
    theme = p.slots.themes[0]
    if not found:
        return _from(
            ctx,
            "ok",
            f"{at['label']} 근처에서 {theme}을(를) 찾지 못했어요.",
            version,
            evidence=_evidence(KAKAO_SOURCE),
        )
    warnings = ["가까운 곳에 없어 조금 더 넓게 찾아봤어요."] if widened else []
    return _from(
        ctx,
        "ok",
        f"{at['label']} 근처 {theme} {len(found)}곳이에요.",
        version,
        places=[
            {
                "name": x["name"],
                "detail": " · ".join(
                    filter(
                        None,
                        [
                            f"약 {_distance(x.get('distance_m'))}"
                            if x.get("distance_m") is not None
                            else None,
                            x["category"],
                        ],
                    )
                ),
                "address": x["address"],
                "url": x["url"],
                "lat": x["lat"],
                "lon": x["lon"],
            }
            for x in found
        ],
        evidence=_evidence(KAKAO_SOURCE),
        warnings=warnings,
        missing_data=["거리는 직선거리예요.", HOURS_UNVERIFIED],
    )


def routes(ctx, p, origin, dest, mobility, r, version) -> dict:
    who = MOBILITY_WORD.get(mobility)
    head = f"{origin['label']}에서 {dest['label']}까지"
    if not r["found"]:
        reasons = sorted({line.lstrip("· ") for x in r["routes"] for line in x["text"] if line})
        return _from(
            ctx,
            "infeasible",
            f"{head} 걷는 길을 찾지 못했어요. " + " ".join(reasons[:2]),
            version,
            routes=r,
        )
    note = f" {who} 조건을 지키는 길만 골랐어요." if who else ""
    return _from(
        ctx,
        "ok",
        f"{head} 걷는 길을 비교했어요.{note}",
        version,
        routes=r,
        evidence=_evidence("오픈스트리트맵 도로·교통사고 다발구역"),
        places=[
            {"name": origin["label"], "detail": "출발", "lat": origin["lat"], "lon": origin["lon"]},
            {"name": dest["label"], "detail": "도착", "lat": dest["lat"], "lon": dest["lon"]},
        ],
    )


# 일정 --------------------------------------------------------------------
def no_candidates(ctx, p, version) -> dict:
    return _from(
        ctx,
        "infeasible",
        "조건에 맞는 곳을 찾지 못했어요. 다른 종류나 출발지로 다시 말씀해 주세요.",
        version,
        evidence=_evidence(KAKAO_SOURCE),
    )


def nothing_fits(ctx, p, obs, version) -> dict:
    if obs.get("stopped_by") == "time":
        msg = "주어진 시간 안에 들를 곳이 없어요. 시간을 늘리거나 들를 곳 수를 줄여 주세요."
    elif obs.get("unreachable"):
        msg = "걸어서 갈 수 있는 후보를 찾지 못했어요."
    else:
        msg = "도착지 쪽으로 가는 길에 들를 만한 곳을 찾지 못했어요."
    return _from(ctx, "infeasible", msg, version)


def validation_failed(ctx, p, check, version) -> dict:
    if "leg_unknown_with_deadline" in check.get("errors", []):
        return _from(ctx, "infeasible",
                     "걷는 시간을 확인하지 못한 구간이 있어 돌아올 시각을 지킬 수 있는지 알 수 없어요. "
                     "일정은 바꾸지 않았어요.", version)
    return _from(
        ctx,
        "error",
        "일정을 확인하는 중 맞지 않는 부분이 있어 저장하지 않았어요. 다시 요청해 주세요.",
        version,
    )


def _stop_view(i: int, st: dict, minutes: int | None) -> dict:
    return {
        "number": i + 1,
        "name": st.get("name") or f"{st['theme']} (다시 찾지 못함)",
        "detail": " · ".join(filter(None, [st.get("category"), st.get("address")])),
        "url": st.get("url"),
        "walk_minutes": minutes,
        "done": bool(st.get("completed")),
        "fixed": bool(st.get("fixed")),
        "lat": st.get("lat"),
        "lon": st.get("lon"),
    }


def _itinerary_view(stops, leg_list, check, end_label) -> dict:
    remaining = [s for s in stops if not s.get("completed")]
    leg_by_ref = {}
    for st, lg in zip(remaining, leg_list, strict=False):
        leg_by_ref[st["ref"]] = lg["minutes"]
    return {
        "stops": [_stop_view(i, st, leg_by_ref.get(st["ref"])) for i, st in enumerate(stops)],
        "back_label": end_label,
        "back_minutes": leg_list[-1]["minutes"] if leg_list else None,
        "total_minutes": check["total_minutes"],
    }


def itinerary(
    ctx,
    p,
    stops,
    leg_list,
    check,
    obs,
    widened,
    s,
    mobility,
    version,
    *,
    created: bool,
    commit: dict,
) -> dict:
    end_label = "출발한 곳으로 돌아오기" if s.round_trip else f"{s.destination_text}까지"
    view = _itinerary_view(stops, leg_list, check, end_label)
    notes = [GREEDY_NOTE]
    missing = [HOURS_UNVERIFIED]
    if not s.stay_minutes:
        missing.append("이동 시간만 계산했고 머무는 시간은 빠져 있어요.")
    if "leg_unknown" in check["warnings"] or "unlocated" in ctx["observations"]:
        missing.append("다시 찾지 못한 장소가 있어 일부 구간 시간은 확인하지 못했어요.")
    warnings = []
    if widened:
        warnings.append("가까운 곳에 후보가 적어 조금 더 넓게 찾아봤어요.")
    if obs.get("unreachable"):
        who = MOBILITY_WORD.get(mobility)
        warnings.append(
            f"{who + ' 조건으로 ' if who else ''}걸어서 갈 수 없는 후보 {obs['unreachable']}곳은 뺐어요."
        )
    if "theme_not_found" in ctx["observations"]:
        warnings.append("요청하신 종류의 새 장소는 찾지 못해 넣지 못했어요.")
    if mobility != MobilityProfile.STANDARD:
        notes.append(
            f"{MOBILITY_WORD[mobility]}로 갈 수 없는 계단 구간을 빼고 계산했어요. "
            "계단 말고 다른 장애물은 자료가 없어 확인하지 못했어요."
        )
    count = len(stops)
    msg = (
        f"{count}곳을 들르는 일정을 만들어 내 여행에 저장했어요."
        if created
        else "말씀하신 대로 일정을 고쳐 저장했어요."
    )
    if s.return_by:
        msg += f" {s.return_by[11:16]}까지 돌아오는 기준이에요."
    return {
        **_from(
            ctx,
            "ok",
            msg,
            version,
            itinerary={**view, "notes": notes},
            places=[
                {"name": x["name"], "detail": f"{x['number']}번", "lat": x["lat"], "lon": x["lon"]}
                for x in view["stops"]
                if x["lat"] is not None
            ],
            evidence=_evidence(KAKAO_SOURCE) + _evidence("오픈스트리트맵 도로"),
            claims=[
                {"text": "장소 이름·주소", "verified": True, "evidence": 0},
                {"text": "구간별 걷는 시간", "verified": "leg_unknown" not in check["warnings"],
                 "evidence": 1 if "leg_unknown" not in check["warnings"] else None},
                {"text": "영업시간", "verified": False, "evidence": None},
            ],
            missing_data=missing,
            warnings=warnings,
        ),
        "commit": commit,
        "options_state": None,
    }


def over_time(ctx, p, stops, leg_list, check, budget_min, version) -> dict:
    over = round(check["total_minutes"] - (budget_min or 0))
    view = _itinerary_view(stops, leg_list, check, "")
    return _from(
        ctx,
        "infeasible",
        f"이대로면 돌아올 시각보다 약 {over}분 늦어요. 고정한 장소는 빼지 않았어요. "
        "다른 장소를 빼거나 돌아올 시각을 늦춰 주세요. 일정은 바꾸지 않았어요.",
        version,
        itinerary={**view, "notes": []},
        questions=[
            {
                "text": "어떻게 할까요?",
                "options": [
                    {"label": f"{x['number']}번 빼기", "reply": f"{x['number']}번 빼줘"}
                    for x in view["stops"]
                    if not x["fixed"] and not x["done"]
                ][:3],
            }
        ],
    )
