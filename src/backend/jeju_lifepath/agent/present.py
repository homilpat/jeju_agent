"""Agent 결과 -> 공통 응답(12.7). 사용자에게 보이는 값(message, questions, itinerary/places의 글자,
warnings, missing_data, claims의 text)은 모두 한국어 문장이다. 상태·ID 같은 내부값은 화면 코드가
분기에만 쓰고 표시하지 않는다(REQUIREMENTS §7)."""

from __future__ import annotations

import re
import statistics
from datetime import UTC, datetime

from jeju_lifepath.agent.kakao import SOURCE as KAKAO_SOURCE
from jeju_lifepath.agent.services import BARRIER_FREE_SOURCE, OLLE_SOURCE, PUBLIC_SOURCES
from jeju_lifepath.presentation.route import PREF_TEXT, _topic
from jeju_lifepath.schemas import MobilityProfile, RoutePreference

FACILITY_WORD = {
    "pharmacy": "약국",
    "toilet": "화장실",
    "wifi": "공공 와이파이",
    "police": "경찰관서",
    "night_pharmacy": "공공심야약국",
    "nursing_room": "수유실",
    "emergency": "응급실",
}
MOBILITY_WORD = {MobilityProfile.STROLLER: "유모차", MobilityProfile.WHEELCHAIR: "휠체어"}
TOOL_WORD = {
    "search_places": "장소 검색",
    "resolve_location": "위치 찾기",
    "find_nearby_facilities": "근처 시설 찾기",
    "compare_routes": "걷는 길 계산",
    "olle_courses": "올레 코스 찾기",
    "barrier_free_places": "무장애 여행지 찾기",
    "get_travel_matrix": "걷는 거리 계산",
    "find_route_stops": "가는 길에 들를 곳 찾기",
    "analyze_area_access": "지역 시설 거리 계산",
}
EMERGENCY_119 = "많이 아프거나 위급하면 걸어가지 말고 바로 119에 전화하렴."
HOURS_UNVERIFIED = "영업시간은 내가 확인하지 못했단다. 가기 전에 카카오맵에서 꼭 확인해 보렴."
HELP = (
    "이런 걸 도와줄 수 있단다: 근처 약국·화장실·와이파이 찾기, 두 곳 사이 걷는 길 비교, "
    "가는 길에 들를 약국·화장실 찾기, 동네에서 약국·화장실이 먼 곳 살펴보기, "
    "들를 곳을 넣은 걷기 일정 만들기·고치기·저장. 예: '애월에서 빵집 두 곳 들르는 코스 짜줘'"
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
        return "따로 찾아보지 않고 답했단다."
    return ", ".join(f"{w} {n}번" for w, n in counts.items()) + " 했단다."


KAKAO_TOOLS = frozenset(
    {
        "search_places",
        "resolve_location",
        "find_nearby_facilities",
        "find_route_stops",
        "analyze_area_access",
        "olle_courses",  # 시작·끝 지점 위치를 카카오 장소 검색으로 찾음
    }
)
STORED_NOTE = "장소 정보가 담긴 답은 저장해 두지 않는단다. 다시 보려면 한 번 더 물어보렴."


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
                {**s, "name": f"{s['number']}번 장소", "detail": "", "address": "", "lat": None,
                 "lon": None}
                for s in resp["itinerary"]["stops"]
            ],
        }
    if resp.get("routes"):
        out["routes"] = {**resp["routes"],
                         "routes": [{**r, "line": []} for r in resp["routes"]["routes"]]}
    return out


def screen_note(screen: dict | None, trip) -> str:
    """화면 맥락과 지금 일정 -> LLM에 넘길 글(사용자에게는 보이지 않음)."""
    stops = (trip[0].get("places") or []) if trip else []
    plan = "지금 이 대화의 일정: " + (
        " / ".join(f"{i}번 {x.get('name', '')}" for i, x in enumerate(stops, 1)) or "아직 없음"
    )
    where = _screen(screen, trip)
    return f"{where}. {plan}" if where else plan


def _screen(screen: dict | None, trip) -> str:
    if not screen:
        return ""
    places = len(trip[0].get("places", [])) if trip else 0
    name = screen.get("name")
    if name == "editor":
        return f"여행 일정 편집(플래너) 화면, 이 여행에 장소 {places}곳"
    if name == "travel":
        stop = (screen.get("travel_stop") or 0) + 1
        return f"여행 안내 화면, 일정 {places}곳 중 {stop}번째 장소에서 다음 장소로 가는 중"
    if name == "planners":
        return "내 여행 목록 화면"
    if name == "chat" and trip:
        return f"AI 대화 화면, 사용자가 고른 여행(장소 {places}곳)을 함께 보는 중"
    return ""


def stage_text(stage: str) -> str:
    return {
        "completed": "다 답했단다.",
        "cancelled": "그만뒀단다.",
        "failed": "끝까지 해내지 못했구나.",
        "interrupted": "하다가 멈췄구나. 이어서 하기를 누르면 다시 계산해 주마.",
    }.get(stage, "살펴보고 있단다.")


# 흐름 제어 ---------------------------------------------------------------
def still_running(run_id: str, conv_id: str) -> dict:
    # still_running: 화면이 실행 상태를 다시 확인하라는 표시(사용자에게 보이지 않음)
    return base(
        run_id, conv_id, "partial", "같은 말을 아직 살펴보고 있단다. 조금 있다가 다시 보렴.", "",
        still_running=True, summary="",
    )


def cancelled(run_id: str, conv_id: str, version: str) -> dict:
    return base(run_id, conv_id, "cancelled", "알았다, 그만두마. 일정은 그대로란다.", version)


def needs_input(run_id, conv_id, questions, version, parse, trip_id, trip_version) -> dict:
    lead = questions[0]["text"] if len(questions) == 1 else "몇 가지만 알려 주렴."
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
        "시간이 너무 오래 걸려 여기서 멈췄단다. 일정은 그대로야. 조건을 조금 줄여서 다시 말해 주렴."
        if kind == "deadline"
        else "같은 문제가 자꾸 생겨서 여기서 멈췄단다. 일정은 그대로야. 조금 있다가 다시 해 보렴."
    )
    return base(
        run_id, conv_id, "partial", msg, version, missing_data=["계산을 다 끝내지 못한 부분이 있단다."]
    )


def tool_failure(run_id, conv_id, kind: str, ctx, version) -> dict:
    msg = {
        "auth_config": "지금은 장소 찾는 곳에 닿질 않는구나(설정 문제). 걷는 길 비교는 계속 쓸 수 있단다.",
        "transient": "장소 찾는 곳이 잠깐 대답을 안 하는구나. 조금 있다가 다시 물어봐 주렴.",
        "timeout": "장소 찾는 곳이 너무 늦게 대답하는구나. 조금 있다가 다시 물어봐 주렴.",
        "unavailable": "지금은 길을 계산할 수가 없구나. 길 자료가 아직 준비되지 않았단다.",
        "invalid_args": "말해 준 내용을 다시 한번 확인해 주렴.",
    }.get(kind, "잠깐 문제가 생겼구나. 다시 한번 해 보렴.")
    return base(run_id, conv_id, "error", msg, version)


def version_conflict(ctx, version) -> dict:
    return _from(
        ctx,
        "error",
        "그사이 다른 곳에서 일정이 바뀌었구나. 최신 일정을 보고 다시 말해 주렴. 이번 변경은 저장하지 않았단다.",
        version,
    )


def unsupported_line(features: list[str]) -> str:
    what = "·".join(features)
    return f"{what}{_topic(what)} 아직 도와주지 못한단다."


def unsupported(ctx, p, version, map_link: dict | None = None) -> dict:
    feats = p.unsupported_features or ["요청하신 기능"]
    msg = unsupported_line(feats)
    # LLM이 붙인 이름("택시 이동" 등)이 아니라 해석한 이동 수단으로 본다
    if "차량 이동" in feats or p.slots.transport in ("car", "bus", "taxi"):
        # 차·대중교통 길은 카카오맵 길찾기로 넘기고, 걷는 길은 제안만(동의 없이 바로 계산하지 않음)
        if map_link:
            msg += " 차나 대중교통 길은 카카오맵 길찾기에서 볼 수 있단다."
        msg += " 걸어서 가는 길은 내가 찾아 줄 수 있어."
        walk = [{"text": msg, "options": [{"label": "걸어서 갈게", "reply": "걸어서 갈게"}]}]
        return _from(ctx, "unsupported", msg, version, questions=walk, places=[map_link] if map_link else [])
    return _from(ctx, "unsupported", msg, version)


def chat(ctx, p, version) -> dict:
    return _from(ctx, "ok", p.reply or HELP, version)


# 안내 --------------------------------------------------------------------
def _evidence(source: str) -> list[dict]:
    return [{"source": source, "checked_at": _now()}]


def place_choices(ctx, p, items, version) -> dict:
    return {
        **_from(
            ctx,
            "needs_input",
            "같은 이름의 장소가 여러 곳이구나. 어느 곳이니?",
            version,
            questions=[
                {
                    "text": "어느 곳을 말하는 거니?",
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
            f"'{p.slots.place_query}'을(를) 찾지 못했구나. 다른 이름으로 알려 주렴.",
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
        return _from(ctx, "ok", "제주 안에서만 찾을 수 있단다.", version)
    if not found:  # 정상적인 0건: 오류와 다른 문구(11.3, AT-06)
        return _from(
            ctx,
            "ok",
            f"{at['label']} 근처에서 {word}을(를) 찾지 못했구나.",
            version,
            evidence=_evidence(source),
            missing_data=[EMERGENCY_119] if p.slots.facility == "emergency" else [],
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
    missing = ["거리는 곧게 잰 거리란다."]
    if p.slots.facility == "pharmacy":
        missing.append(HOURS_UNVERIFIED)
    if p.slots.facility == "night_pharmacy":
        missing.append("운영 시간은 바뀔 수 있단다. 가기 전에 전화로 확인해 보렴.")
    if p.slots.facility == "emergency":
        missing.append(EMERGENCY_119)
    if p.slots.facility == "nursing_room":
        missing.append("기관 안에 있는 수유실이라 여는 시간에만 쓸 수 있단다. 가기 전에 그 기관에 물어보렴.")
    if r.get("partial"):
        missing.append("후보가 많아서 일부만 살펴봤단다.")
    return _from(
        ctx,
        "ok",
        f"{at['label']}에서 가까운 {word} {len(places)}곳이란다.",
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


YES = {"O", "Y", "가능", "있음", "○", "●"}
FIRST_SENTENCE = re.compile(r"(?<=[다요])\.\s*")


def access_bits(x: dict) -> list[str]:
    """한 장소의 접근성 요약 낱말들(조사 자료에 있는 것만)."""
    doc = x.get("doc") or {}
    bits = []
    if doc.get("disabled_toilet") == "있음" or x.get("toilet_points"):
        bits.append("장애인 화장실 있음")
    if doc.get("disabled_parking") == "있음":
        bits.append("장애인 주차장 있음")
    if "휠체어(수동)" in doc.get("audience", []):
        bits.append("휠체어 추천" + (" (보호자와 함께)" if "휠체어(수동)" in doc.get("companion", []) else ""))
    if x.get("barrier_points"):
        bits.append(f"계단·경사 주의 지점 {x['barrier_points']}곳")
    return bits


def kg_places(ctx, results: list[dict], version) -> dict:
    """지식그래프 질의 결과(공공 무장애 조사 자료) 카드."""
    def detail(x):
        bits = [f"약 {_distance(x['distance_m'])}" if x["distance_m"] is not None else None,
                "장애인 화장실 있음" if x["disabled_toilet"] == "있음" else None,
                "장애인 주차장 있음" if x["disabled_parking"] == "있음" else None,
                f"계단·경사 주의 지점 {x['barrier_points']}곳" if x["barrier_points"] else "주의 지점 기록 없음",
                "조사자가 걸은 코스 있음" if x["has_course"] else None]
        return " · ".join(filter(None, bits))

    return _from(
        # 공공자료 이름이라 문장에 넣는다(대화 기록에 남아 "거기 중 첫 번째"를 알아듣게)
        ctx, "ok", f"조건에 맞는 무장애 여행지 {len(results)}곳이란다: {', '.join(x['name'] for x in results)}.",
        version,
        places=[{"name": x["name"], "detail": detail(x), "address": x["address"], "url": None,
                 "lat": x["lat"], "lon": x["lon"]} for x in results],
        evidence=_evidence(BARRIER_FREE_SOURCE),
        missing_data=["무장애 조사는 2019~2021년 자료라 지금과 다를 수 있단다. 가기 전에 한 번 더 확인해 보렴."],
    )


def barrier_free(ctx, p, at, r, version) -> dict:
    """무장애 여행지: 가까운 곳 목록 또는 한 곳 상세(접근성 조사 자료)."""
    found = r["places"]
    evidence = _evidence(BARRIER_FREE_SOURCE)
    dated = "무장애 조사는 2019~2021년 자료라 지금과 다를 수 있단다. 가기 전에 한 번 더 확인해 보렴."
    if not found:
        what = f"'{p.slots.place_query}'의 " if p.slots.place_query else ""
        return _from(ctx, "ok", f"{what}무장애 여행 정보는 찾지 못했구나.", version, evidence=evidence)
    if not r["detail"]:
        places = [{"name": x["name"], "detail": " · ".join([f"약 {_distance(x['distance_m'])}", *access_bits(x)]),
                   "address": x.get("address") or "", "url": None, "lat": x["lat"], "lon": x["lon"]}
                  for x in found]
        return _from(ctx, "ok", f"{at['label']}에서 가까운 무장애 여행지 {len(places)}곳이란다.", version,
                     places=places, evidence=evidence, missing_data=["거리는 곧게 잰 거리란다.", dated])
    x = found[0]
    doc = x.get("doc") or {}
    first = FIRST_SENTENCE.split(doc.get("summary") or "")[0].strip()
    bits = access_bits(x)
    msg = f"{x['name']}의 무장애 여행 정보란다." + (f" {first}." if first else "")
    if bits:
        msg += " " + ", ".join(bits) + "."
    places = [{"name": x["name"], "detail": doc.get("course") or "", "address": x.get("address") or "",
               "url": None, "lat": x["lat"], "lon": x["lon"]}]
    # 설명이 있는 지점만 카드로(빈 카드가 줄지어 보이지 않게), 많으면 앞의 8곳. shortcut: 8은 화면 보기 좋은 정도
    places += [{"name": pt["label"] or "지점", "detail": pt["info"], "address": "", "url": None,
                "lat": pt["lat"], "lon": pt["lon"]} for pt in x.get("points", []) if pt["info"]][:8]
    friendly = [f"{n['name']}({n['kind']})" for n in x.get("nearby", []) if n["wheelchair"] in YES][:4]
    notes = list(doc.get("notes", []))[:3]
    if doc.get("hours"):
        notes.append(f"관람시간 {doc['hours']}")
    if friendly:
        notes.append("근처에 휠체어로 들어갈 수 있는 곳: " + ", ".join(friendly))
    extra = {}
    if x.get("track"):  # 조사자가 실제로 걸은 무장애 코스(GPS)를 지도에 선으로
        extra["track"] = x["track"]
        notes.insert(0, "지도의 초록 선은 조사자가 실제로 걸어 본 무장애 코스란다."
                     + (f" 오르내림은 약 {x['climb_m']}m야." if x.get("climb_m") else ""))
    return _from(ctx, "ok", msg, version, places=places, evidence=evidence,
                 missing_data=[*notes, dated], **extra)


def olle(ctx, p, r, version) -> dict:
    """올레 코스 안내. 코스를 말했으면 그 코스, 아니면 가까운 코스(시작점 기준)."""
    courses = r["courses"]
    if not courses:
        return _from(ctx, "ok", "그런 올레 코스는 찾지 못했구나. 1코스부터 21코스까지, 1-1·7-1처럼 말해 주렴.",
                     version, evidence=_evidence(OLLE_SOURCE))

    def pins(c):
        out = []
        for label, name, spot in (("시작", c["start"], c["start_at"]), ("끝", c["end"], c["end_at"])):
            if spot and name:
                out.append({"name": f"올레 {c['course']} {label}: {name}",
                            "detail": f"{c['length']} · {c['time']}", "address": "", "url": None,
                            "lon": spot[0], "lat": spot[1]})
        return out

    if len(courses) == 1 and courses[0]["distance_m"] is None:
        c = courses[0]
        ends = f"{c['start']}에서 시작해서 {c['end']}에서 끝난단다." if c["end"] else f"{c['start']}을(를) 돈단다."
        msg = f"올레 {c['course']}({c['name']})는 {c['length']}, {c['time']} 걸리는 길이란다. {ends}"
        places = pins(c)
    else:
        msg = f"가까운 올레 코스 {len(courses)}개란다. 시작점까지 곧게 잰 거리야."
        places = [{**pin, "detail": f"{pin['detail']} · 시작점까지 약 {_distance(c['distance_m'])}"}
                  for c in courses for pin in pins(c)[:1]]
    return _from(
        ctx, "ok", msg, version, places=places, evidence=_evidence(OLLE_SOURCE),
        missing_data=["걸리는 시간은 걷는 사람마다 다르단다.", "지도 위치는 시작·끝 지점 이름으로 찾은 거란다."],
    )


def theme_places(ctx, p, at, found, widened, version) -> dict:
    theme = p.slots.themes[0]
    if not found:
        return _from(
            ctx,
            "ok",
            f"{at['label']} 근처에서 {theme}을(를) 찾지 못했구나.",
            version,
            evidence=_evidence(KAKAO_SOURCE),
        )
    warnings = ["가까운 곳에 없어서 조금 더 넓게 찾아봤단다."] if widened else []
    return _from(
        ctx,
        "ok",
        f"{at['label']} 근처 {theme} {len(found)}곳이란다.",
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
        missing_data=["거리는 곧게 잰 거리란다.", HOURS_UNVERIFIED],
    )


def route_preference_question() -> dict:
    return {
        "text": "어떤 길로 안내해 줄까?",
        "options": [
            {"label": PREF_TEXT[RoutePreference.SAFETY], "reply": "안전환경 우선 경로로"},
            {"label": PREF_TEXT[RoutePreference.SHORTEST], "reply": "최단 경로로"},
            {"label": PREF_TEXT[RoutePreference.BALANCED], "reply": "균형 경로로"},
        ],
    }


def routes(ctx, p, origin, dest, mobility, r, version, pref: RoutePreference) -> dict:
    who = MOBILITY_WORD.get(mobility)
    head = f"{origin['label']}에서 {dest['label']}까지"
    if not r["found"]:
        reasons = sorted({line.lstrip("· ") for x in r["routes"] for line in x["text"] if line})
        return _from(
            ctx,
            "infeasible",
            f"{head} 걷는 길을 찾지 못했구나. " + " ".join(reasons[:2]),
            version,
            routes=r,
        )
    note = (
        " 계단은 피했지만, 휠체어로 다닐 수 있는지 확인된 길은 일부뿐이란다. "
        "길마다 확인 안 된 비율을 보고, 턱이나 경사는 조심하렴."
        if mobility == MobilityProfile.WHEELCHAIR
        else f" {who} 조건을 지키는 길만 골랐단다." if who else ""
    )
    chosen = next((x for x in r["routes"] if x["kind"] == pref.value and x["found"]), None)
    lead = (
        f"{head} {chosen['title']}를 지도에 그려 줬단다. 다른 길과도 비교해 보렴."
        if chosen
        else f"{head} {PREF_TEXT[pref]}는 찾지 못해서 다른 길을 보여 주마."
    )
    return _from(
        ctx,
        "ok",
        f"{lead}{note}",
        version,
        routes=r,
        # 화면이 처음 그릴 경로(경로 종류 버튼과 맞추는 값, 화면에 표시하지 않음)
        chosen_route=chosen["kind"] if chosen else next(x["kind"] for x in r["routes"] if x["found"]),
        evidence=_evidence("오픈스트리트맵 도로·교통사고 다발구역"),
        places=[
            {"name": origin["label"], "detail": "출발", "lat": origin["lat"], "lon": origin["lon"]},
            {"name": dest["label"], "detail": "도착", "lat": dest["lat"], "lon": dest["lon"]},
        ],
    )


def _minutes(m: float, m_per_min: float) -> int:
    return round(m / m_per_min)


def _what(p) -> str:
    return FACILITY_WORD[p.slots.facility] if p.slots.facility else p.slots.themes[0]


def _pin(label: str, at: dict) -> dict:
    # 지도에만 찍는 점(대화 카드로 보이지 않음)
    return {"name": label, "detail": "", "lat": at["lat"], "lon": at["lon"], "pin_only": True}


def _source_note(source: str, word: str) -> str:
    if source == "kakao":
        return f"카카오맵에 등록된 {word} 기준이란다. 등록 안 된 곳이 더 있을 수 있어."
    return f"공공데이터에 등록된 {word} 기준이란다. 등록 안 된 곳이 더 있을 수 있어."


def route_stops(ctx, p, origin, dest, mobility, r, m_per_min, version) -> dict:
    word = _what(p)
    head = f"{origin['label']}에서 {dest['label']}까지"
    if r["status"] == "no_route":
        return _from(ctx, "infeasible", f"{head} 걸어서 가는 길을 찾지 못했구나.", version)
    source = KAKAO_SOURCE if r["source"] == "kakao" else PUBLIC_SOURCES.get(p.slots.facility, "")
    missing = [_source_note(r["source"], word)]
    if r["truncated"]:
        missing.append(
            "찾은 곳이 많아서 도착지에서 가까운 45곳 안에서만 골랐단다."
            if r["source"] == "kakao"
            else "시간 안에 위치를 다 찾지 못해서 화장실 일부만 살펴봤단다."
        )
    if r.get("unlocated"):
        missing.append(f"주소로 위치를 찾지 못한 {word} {r['unlocated']}곳(같은 시 전체 기준)은 살펴보지 못했단다.")
    if r["unreachable"]:
        missing.append(f"걸어서 닿는 길을 찾지 못한 {r['unreachable']}곳은 뺐단다.")
    if p.slots.facility == "pharmacy":
        missing.append(HOURS_UNVERIFIED)
    pins = [_pin(f"출발 {origin['label']}", origin), _pin(f"도착 {dest['label']}", dest)]
    if not r["stops"]:  # 정상적인 0건(11.3): 오류와 다른 문구
        return _from(
            ctx,
            "ok",
            f"{head} 가는 길에 들를 만한 {word}을(를) 찾지 못했구나. 바로 가는 길은 '길 알려줘'로 물어보렴.",
            version,
            places=pins,
            evidence=_evidence(source),
            missing_data=missing,
        )
    places = [
        {
            "name": x["name"],
            "detail": f"들르면 약 {_minutes(x['detour_m'], m_per_min)}분 더 걸려요 · "
            f"출발에서 걸어서 약 {_minutes(x['from_start_m'], m_per_min)}분",
            "address": x.get("address", ""),
            "url": x.get("url"),
            "lat": x["lat"],
            "lon": x["lon"],
        }
        for x in r["stops"]
    ]
    return _from(
        ctx,
        "ok",
        f"{head} 가는 길에 들를 수 있는 {word} {len(places)}곳이란다. 덜 돌아가는 순서로 놓았어. "
        "어디에 들를지는 네가 골라 보렴.",
        version,
        places=places + pins,
        evidence=_evidence(source) + _evidence("오픈스트리트맵 도로"),
        missing_data=missing,
        claims=[
            {"text": f"{word} 위치", "verified": True, "evidence": 0},
            {"text": "더 걸리는 시간(가장 짧은 길 기준)", "verified": True, "evidence": 1},
        ],
    )


AREA_SOURCE = "행정동 경계(통계청 SGIS, 가공 vuski/admdongkor, CC BY 4.0)"


def areas_unavailable(ctx, version) -> dict:
    return _from(ctx, "error", "동네 경계 자료가 준비되지 않아서 동네별로는 살펴볼 수가 없구나.", version)


def outside_areas(ctx, place, version) -> dict:
    return _from(ctx, "ok", f"{place['label']}이(가) 어느 동네에 있는지 찾지 못했구나. 제주 안 동네 이름으로 알려 주렴.", version)


def area_access(ctx, p, name, place, r, mobility, m_per_min, version) -> dict:
    word = FACILITY_WORD[p.slots.facility]
    where = f"{place['label']}이(가) 있는 {name}" if place else name
    source = KAKAO_SOURCE if r["source"] == "kakao" else PUBLIC_SOURCES.get(p.slots.facility, "")
    if r["status"] == "truncated":
        return _from(
            ctx,
            "partial",
            f"{where}을(를) 살펴보려 했는데 시간 안에 {word}을(를) 다 세지 못했구나. "
            "다 세지도 않고 먼 곳을 말하면 틀릴 수 있어서 계산하지 않았단다. 조금 있다가 다시 물어봐 주렴.",
            version,
            evidence=_evidence(source),
        )
    if r["status"] == "unsupported_mobility":
        return _from(
            ctx,
            "infeasible",
            f"{MOBILITY_WORD.get(mobility, '이 이동 방법')}로 지나갈 수 있는지 확인된 길 자료가 없어서 계산하지 않았단다.",
            version,
        )
    notes = [
        _source_note(r["source"], word),
        *([f"주소로 위치를 찾지 못한 {word} {r['unlocated']}곳(같은 시 전체 기준)은 살펴보지 못했단다."]
          if r.get("unlocated") else []),
        "사는 사람 수 같은 자료는 넣지 않아서 어디가 더 급한지는 내가 판단하지 않는단다.",
        f"{name} 경계 밖 {word}은(는) 세지 않아서 경계 근처는 실제보다 멀게 나올 수 있단다.",
    ]
    facilities = [_pin(f"{word} {x['name']}".strip(), x) for x in r["facilities"]]
    if not r["facilities"]:
        return _from(
            ctx,
            "ok",
            f"{where}을(를) 살펴봤는데 동 안에 등록된 {word}을(를) 찾지 못했구나.",
            version,
            places=[_pin(place["label"], place)] if place else [],
            evidence=_evidence(source) + _evidence(AREA_SOURCE),
            missing_data=notes,
        )
    reach = [(pt, d) for pt, d in r["points"] if d is not None]
    cut_off = len(r["points"]) - len(reach)
    if not reach:
        return _from(
            ctx,
            "ok",
            f"{where} 안에는 {word}까지 걸어서 이어지는 길 자료가 없어서 계산하지 못했구나.",
            version,
            places=facilities,
            evidence=_evidence(source),
            missing_data=notes,
        )
    median = _minutes(statistics.median(d for _, d in reach), m_per_min)
    far_pt, far_d = max(reach, key=lambda x: x[1])
    if cut_off:
        notes.append(f"길이 끊겨 {word}까지 걸어서 닿지 못한 지점 {cut_off}곳은 셈에서 뺐단다.")
    notes.append("길 위 지점은 길이 만나거나 꺾이거나 끝나는 곳이란다. 시설에서 길까지 떨어진 거리는 빼고 쟀어.")
    return _from(
        ctx,
        "ok",
        f"{where}을(를) 살펴봤단다. {word} {len(facilities)}곳이 있고, 길 위 지점 {len(reach)}곳에서 "
        f"가장 가까운 {word}까지 걸어서 절반은 약 {median}분 안이야. 가장 먼 곳은 약 "
        f"{_minutes(far_d, m_per_min)}분이라 지도에 표시해 뒀단다.",
        version,
        places=[
            {
                "name": f"{word}에서 가장 먼 곳",
                "detail": f"가장 가까운 {word}까지 걸어서 약 {_minutes(far_d, m_per_min)}분",
                "lat": far_pt[1],
                "lon": far_pt[0],
            },
            *facilities,
        ],
        evidence=_evidence(source) + _evidence("오픈스트리트맵 도로") + _evidence(AREA_SOURCE),
        missing_data=notes,
        claims=[
            {"text": f"{word} 위치", "verified": True, "evidence": 0},
            {"text": "걷는 거리(가장 짧은 길 기준)", "verified": True, "evidence": 1},
            {"text": "동네 범위", "verified": True, "evidence": 2},
        ],
    )


def save_mode_question() -> dict:
    return {
        "text": "원래 여행에 덮어쓸까, 새 여행으로 저장할까?",
        "options": [
            {"label": "원래 여행에 덮어쓰기", "reply": "덮어써서 저장해줘"},
            {"label": "새 여행으로 저장", "reply": "새 여행으로 저장해줘"},
        ],
    }


def nothing_to_save(ctx, had_trip: bool, version) -> dict:
    msg = (
        "이미 내 여행에 저장된 일정이란다."
        if had_trip
        else "저장할 일정이 없구나. 먼저 일정을 만들어 보렴. 예: 애월에서 빵집 두 곳 들르는 코스 짜줘"
    )
    return _from(ctx, "ok", msg, version)


def save_outdated(ctx, version) -> dict:
    return _from(
        ctx,
        "needs_input",
        "그사이 원래 여행이 바뀌어서 덮어쓰지 않았단다. 새 여행으로 저장할까?",
        version,
        questions=[
            {
                "text": "어떻게 할까?",
                "options": [{"label": "새 여행으로 저장", "reply": "새 여행으로 저장해줘"}],
            }
        ],
    )


def saved(ctx, trip_id: int, draft_id: int, overwrite: bool, version) -> dict:
    msg = "원래 여행에 덮어써서 저장해 뒀단다." if overwrite else "내 여행에 잘 저장해 뒀단다."
    # saved_trip·saved_draft: 화면이 그 초안의 저장 버튼을 '저장됨'으로 바꾸는 데만 쓴다(표시하지 않음)
    return _from(ctx, "ok", msg, version, saved_trip=trip_id, saved_draft=draft_id, trip_id=trip_id)


# 일정 --------------------------------------------------------------------
def no_candidates(ctx, p, version) -> dict:
    return _from(
        ctx,
        "infeasible",
        "조건에 맞는 곳을 찾지 못했구나. 다른 종류나 출발지로 다시 말해 주렴.",
        version,
        evidence=_evidence(KAKAO_SOURCE),
    )


def nothing_fits(ctx, p, obs, version) -> dict:
    if obs.get("stopped_by") == "time":
        msg = "주어진 시간 안에 들를 곳이 없구나. 시간을 늘리거나 들를 곳 수를 줄여 보렴."
    elif obs.get("unreachable"):
        msg = "걸어서 갈 수 있는 곳을 찾지 못했구나."
    else:
        msg = "도착지 쪽으로 가는 길에 들를 만한 곳을 찾지 못했구나."
    return _from(ctx, "infeasible", msg, version)


def too_far_question(walk_min: int) -> dict:
    """돌아올 시각 없이 걷기만 하루를 넘는 일정: 숫자는 계산 결과 그대로, 고르는 건 사용자."""
    hours = round(walk_min / 60)
    return {"text": f"고른 곳들이 멀어서 걷는 시간만 {hours}시간쯤 된단다. 걸어서 하루에 다니기는 어렵겠구나. "
                    "한 곳만 들를까, 아니면 조건을 줄여서 가까운 곳으로 찾아볼까?",
            "options": [{"label": "한 곳만 들를게", "reply": "한 곳만 들를게"},
                        {"label": "가까운 곳으로 찾아줘", "reply": "조건을 줄여서 가까운 곳으로 찾아줘"}]}


def validation_failed(ctx, p, check, version) -> dict:
    if "leg_unknown_with_deadline" in check.get("errors", []):
        return _from(ctx, "infeasible",
                     "걷는 시간을 확인하지 못한 구간이 있어서 돌아올 시각을 지킬 수 있는지 모르겠구나. "
                     "일정은 바꾸지 않았단다.", version)
    return _from(
        ctx,
        "error",
        "일정을 확인하다가 맞지 않는 부분이 있어서 저장하지 않았단다. 다시 말해 주렴.",
        version,
    )


def _stop_view(i: int, st: dict, minutes: int | None) -> dict:
    return {
        "number": i + 1,
        "name": st.get("name") or f"{st['theme']} (다시 찾지 못함)",
        "detail": " · ".join(filter(None, [st.get("category"), st.get("access"), st.get("address")])),
        "address": st.get("address") or "",  # 기사님께 보여주기(화면이 큰 글씨로)
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
    next_day: dict | None = None,
) -> dict:
    end_label = "출발한 곳으로 돌아오기" if s.round_trip else f"{s.destination_text}까지"
    view = _itinerary_view(stops, leg_list, check, end_label)
    # 이 일정에만 해당하는 말만 붙인다. 영업시간 미확인·머무는 시간 빠짐·순서가 최선이라 장담 못 함 같은
    # 모든 일정에 같은 말은 시작 화면 안내로 한 번만(답마다 되풀이하면 정작 중요한 말이 묻힘)
    notes = []
    if ctx.get("start_label") == "지금 위치":
        notes.append(f"출발점은 {ctx['start_label']}로 잡았단다. 다른 곳에서 출발하면 말해 주렴.")
    missing = []
    if "leg_unknown" in check["warnings"] or "unlocated" in ctx["observations"]:
        missing.append("다시 찾지 못한 장소가 있어서 일부 구간 시간은 확인하지 못했구나.")
    warnings = []
    if widened:
        warnings.append("가까운 곳에 후보가 적어서 조금 더 넓게 찾아봤단다.")
    if obs.get("unreachable"):
        who = MOBILITY_WORD.get(mobility)
        warnings.append(
            f"{who + ' 조건으로 ' if who else ''}걸어서 갈 수 없는 후보 {obs['unreachable']}곳은 뺐단다."
        )
    # 부탁한 수보다 적게 넣었으면 왜 그런지(못 찾은 종류) 말한다. 조용히 줄이지 않는다
    if created and s.stop_count and len(stops) < s.stop_count:
        got = {x["theme"] for x in stops}
        lacking = [t for t in s.themes if t not in got]
        if obs.get("stopped_by") == "time":
            why = "돌아올 시간 안에 더 넣을 수 없어서 "
        elif lacking:  # 후보가 다 떨어져 멈춤: 아직 못 넣은 종류를 밝힌다
            why = f"{'·'.join(lacking)}은(는) 가는 방향에서 들를 만한 곳을 찾지 못해서 "
        else:
            why = "더 들를 만한 곳을 찾지 못해서 "
        warnings.append(f"{s.stop_count}곳을 부탁했는데 {why}{len(stops)}곳만 넣었단다.")
    if "theme_not_found" in ctx["observations"]:
        warnings.append("말한 종류의 새 장소는 찾지 못해서 넣지 못했구나.")
    if mobility != MobilityProfile.STANDARD:
        notes.append(f"{MOBILITY_WORD[mobility]}로 못 가는 계단 길은 뺐단다. 계단 말고 다른 장애물은 확인하지 못했어.")
        if not any(x.get("access") for x in stops):  # 무장애 여행지로 짠 일정이 아니면 그쪽도 있다고 알려 줌
            notes.append(f"{MOBILITY_WORD[mobility]}로 다니기 좋은 무장애 여행지로도 짜 줄 수 있단다.")
    count = len(stops)
    msg = (
        f"좋은 생각이야! {count}곳을 들르는 일정을 짜 봤단다. 마음에 들면 내 여행에 저장해 두렴."
        if created
        else "말한 대로 일정을 고쳐 봤단다. 마음에 들면 내 여행에 저장해 두렴."
    )
    if s.return_by:
        msg += f" {s.return_by[11:16]}까지 돌아오는 기준이야."
    if s.day_no:
        msg = f"{s.day_no}일차 일정이란다. " + msg
    questions = []
    if next_day:  # 여러 날: 하루씩 차례로
        what = "·".join(next_day["themes"])
        msg += f" 하루씩 짜 줄게. 다음은 {next_day['day']}일차({what})란다."
        questions = [{"text": f"{next_day['day']}일차도 짜 줄까?",
                      "options": [{"label": f"{next_day['day']}일차 짜기",
                                   "reply": f"{next_day['day']}일차 일정도 짜줘"}]}]
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
            questions=questions,
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
        f"이대로면 돌아올 시각보다 약 {over}분 늦겠구나. 고정한 장소는 빼지 않았단다. "
        "다른 장소를 빼거나 돌아올 시각을 늦춰 보렴. 일정은 바꾸지 않았단다.",
        version,
        itinerary={**view, "notes": []},
        questions=[
            {
                "text": "어떻게 할까?",
                "options": [
                    {"label": f"{x['number']}번 빼기", "reply": f"{x['number']}번 빼줘"}
                    for x in view["stops"]
                    if not x["fixed"] and not x["done"]
                ][:3],
            }
        ],
    )
