"""자연어 요청 -> ParseResult (규칙 기반). LLM이 없거나 실패해도 이 파서로 동작한다(AG-204).

시각은 언제나 이 규칙으로 푼다: 서버 시각, Asia/Seoul 기준(AG-105). LLM 결과의 시각은 쓰지 않는다.
shortcut: 정해진 낱말·어미 목록으로 의도를 추정하는 휴리스틱이다. 목록에 없는 표현은 놓치며,
놓친 요청은 "chat"(도움말)로 떨어진다. 실 사용 문장을 모아 목록을 늘리거나 LLM 모드로 보완한다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from jeju_lifepath.agent.models import Change, ParseResult, Slots
from jeju_lifepath.schemas import MobilityProfile, RoutePreference

# 한국은 1988년 이후 일광절약시간이 없어 고정 +09:00이 Asia/Seoul과 같다(tzdata 의존성 없이)
SEOUL = timezone(timedelta(hours=9), "Asia/Seoul")

# 사용자 표현 -> 카카오 검색어. 여기 없는 테마는 찾지 않는다(장소를 지어내지 않기)
THEMES = {
    "빵집": "빵집",
    "베이커리": "빵집",
    "빵": "빵집",
    "카페": "카페",
    "커피": "카페",
    "바다": "해수욕장",
    "해변": "해수욕장",
    "해수욕장": "해수욕장",
    "바닷가": "해수욕장",
    "오름": "오름",
    "맛집": "맛집",
    "식당": "맛집",
    "점심": "맛집",
    "저녁밥": "맛집",
    "밥": "맛집",
    "시장": "시장",
    "박물관": "박물관",
    "미술관": "미술관",
    "공원": "공원",
    "숲": "숲길",
}
FACILITIES = {
    "약국": "pharmacy",
    "화장실": "toilet",
    "와이파이": "wifi",
    "wifi": "wifi",
    "파출소": "police",
    "지구대": "police",
    "경찰": "police",
}
UNSUPPORTED = {
    "차량 이동": r"자동차|렌터카|렌트카|운전|택시|버스|(?<!유모)차로|차 타고",
    "예약·결제": r"예약|결제|예매",
    "인물 관련 장소": r"연예인|아이돌|촬영지|배우가",
    "메뉴·가격 안내": r"메뉴|가격|얼마",
}
NUM_WORDS = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8}
HERE = r"여기|이 근처|이근처|근처|주변|지금 위치|현재 위치|내 위치|가까운"
# 이름이 아닌 낱말: "XX에서"의 XX로 잡히면 안 됨
NOT_PLACE = {
    "여기",
    "거기",
    "이",
    "그",
    "지금",
    "내",
    "현재",
    "근처",
    "주변",
    "이곳",
    "렌터카",
    "렌트카",
    "택시",
    "버스",
    "자동차",
    "차",
    "유모차",
    "휠체어",
    "걸어서",
}
LODGING = {"숙소", "호텔", "집", "펜션", "게스트하우스"}

PLAN_WORDS = r"짜\s*줘|짜줘|짜 줘|일정|코스|투어|돌아오|들르|들렀|순례|계획"
MODIFY_WORDS = r"빼|제외|유지|고정|먼저|부터|다녀왔|갔다\s*왔|들렀어|방문했|바꿔|추가|넣어|대신"
ROUTE_WORDS = r"가는\s*길|가는 법|경로|길\s*알려|길 찾|어떻게 가|까지 가|으로 가|로 가"


def _num(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    return NUM_WORDS.get(word)


def _hours_minutes(text: str) -> int | None:
    m = re.search(r"(\d+|한|두|세|네|다섯|여섯)\s*시간\s*(반)?", text)
    mins = 0
    if m and _num(m.group(1)):
        mins = _num(m.group(1)) * 60 + (30 if m.group(2) else 0)
    m2 = re.search(r"(\d+)\s*분(?!\s*씩)", text)
    if m2 and not re.search(r"\d+\s*분\s*(씩|정도\s*씩|머물|체류)", text):
        mins += int(m2.group(1))
    return mins or None


def resolve_clock(text: str, now: datetime) -> tuple[str | None, str | None, str | None, list[str]]:
    """문장 속 날짜·시각 -> (date, depart_at, return_by, 모호성). 시각은 ISO(+09:00)."""
    ambiguities: list[str] = []
    day = now.date()
    if "모레" in text:
        day += timedelta(days=2)
    elif "내일" in text:
        day += timedelta(days=1)
    date_slot = day.isoformat() if re.search(r"오늘|내일|모레", text) else None

    m = re.search(
        r"(오전|오후|저녁|밤|아침|새벽)?\s*(\d{1,2})\s*(?:시\s*(반|(\d{1,2})\s*분)?|:(\d{2}))", text
    )
    midnight = "자정" in text
    if not m and not midnight:
        return date_slot, None, None, ambiguities
    after = text[m.end() :] if m else text[text.index("자정") + 2 :]
    is_return = bool(re.match(r"\s*(까지|전에|전까지)", after)) or bool(
        re.search(r"돌아|복귀|도착", text)
    )
    if midnight:
        at = datetime.combine(day + timedelta(days=1), datetime.min.time(), SEOUL)
    else:
        meridiem, hour = m.group(1), int(m.group(2))
        minute = 30 if m.group(3) == "반" else int(m.group(4) or m.group(5) or 0)
        if hour > 24 or minute > 59:
            return date_slot, None, None, ["시각을 다시 알려 주세요."]
        base = datetime.combine(day, datetime.min.time(), SEOUL)
        if meridiem in ("오후", "저녁", "밤") and hour < 12:
            hour += 12
        elif meridiem in ("오전", "아침", "새벽") and hour == 12:
            hour = 0
        if meridiem or hour >= 12 or hour == 0:
            at = base + timedelta(hours=hour, minutes=minute)
        else:
            # "6시": 오전·오후 중 지금 이후인 쪽이 하나뿐이면 그것, 둘 다면 묻는다
            cands = [base + timedelta(hours=h, minutes=minute) for h in (hour, hour + 12)]
            future = [c for c in cands if c > now]
            if len(future) == 1:
                at = future[0]
            else:
                ambiguities.append(f"오전 {hour}시인지 오후 {hour}시인지 알려 주세요.")
                return date_slot, None, None, ambiguities
    if at <= now:
        ambiguities.append("이미 지난 시각이에요. 날짜나 시각을 다시 알려 주세요.")
        return date_slot, None, None, ambiguities
    iso = at.isoformat()
    return (
        date_slot or at.date().isoformat(),
        None if is_return else iso,
        iso if is_return else None,
        ambiguities,
    )


def _stop_number(text: str) -> int | None:
    m = re.search(r"(\d+)\s*번", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(첫|두|세|네|다섯|여섯)\s*번째", text)
    if m:
        return {"첫": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6}[m.group(1)]
    return None


def _changes(text: str, themes: list[str]) -> tuple[list[Change], list[str]]:
    """일정 수정 지시. 대상이 불분명하면 바꾸지 않고 모호성으로 돌려준다(AT-03)."""
    target = _stop_number(text)
    last = target is None and "마지막" in text
    if re.search(r"빼|제외|유지|고정|다녀왔|갔다\s*왔|들렀어|방문했", text) and not (
        target or last
    ):
        return [], ["몇 번째 장소를 말씀하시는지 번호로 알려 주세요."]
    changes: list[Change] = []
    for op, pat in (
        ("exclude", r"빼|제외"),
        ("keep", r"유지|고정"),
        ("complete", r"다녀왔|갔다\s*왔|들렀어|방문했"),
    ):
        if re.search(pat, text):
            changes.append(Change(op=op, target=target, last=last))
    if themes and re.search(r"먼저|부터", text):
        changes.append(Change(op="prioritize", theme=themes[0]))
    elif themes and re.search(r"추가|넣어|도 가|도 들", text):
        changes.extend(Change(op="add_theme", theme=th) for th in themes)
    return changes, []


def _place_before(text: str, markers: str) -> str | None:
    m = re.search(rf"([가-힣A-Za-z0-9]+(?:\s[가-힣A-Za-z0-9]+)?)\s*(?:{markers})", text)
    if not m:
        return None
    word = m.group(1).split()[-1]
    if len(word) > 2 and word[-1] in "은는":  # "한림공원은" -> "한림공원" ("마을"의 을은 남김)
        word = word[:-1]
    if word in NOT_PLACE or word in THEMES or word in FACILITIES:
        return None
    return word


def parse_rules(text: str, now: datetime, has_trip: bool = False) -> ParseResult:
    raw = text.strip()
    t = raw.lower()
    slots = Slots()
    sources: dict[str, str] = {}

    def put(name: str, value) -> None:
        setattr(slots, name, value)
        sources[name] = "explicit"

    unsupported = [label for label, pat in UNSUPPORTED.items() if re.search(pat, t)]
    if "차량 이동" in unsupported:
        put("transport", "car")

    for word, kind in FACILITIES.items():
        if word in t:
            put("facility", kind)
            break
    themes: list[str] = []
    for word, keyword in THEMES.items():
        if word in raw and keyword not in themes:
            themes.append(keyword)
    if themes:
        put("themes", themes)

    if "유모차" in raw:
        put("mobility", MobilityProfile.STROLLER)
    elif "휠체어" in raw:
        put("mobility", MobilityProfile.WHEELCHAIR)
    if re.search(r"cctv", t):
        put("prefer_cctv", True)
    if re.search(r"최단|빠른|빨리", raw):
        put("route_preference", RoutePreference.SHORTEST)
    elif re.search(r"안전", raw):
        put("route_preference", RoutePreference.SAFETY)
    elif "균형" in raw:
        put("route_preference", RoutePreference.BALANCED)

    if re.search(HERE, raw):
        put("use_current_location", True)
    loc = _place_before(raw, "에서|근처|주변|부근")
    if loc and loc not in LODGING:
        put("location_text", loc)
        if not re.search(r"여기|지금 위치|현재 위치|내 위치", raw):
            slots.use_current_location = False
            sources.pop("use_current_location", None)
    dest = _place_before(raw, "까지|으로 가|로 가|에 가")
    if dest and not re.match(r"\d", dest) and not re.search(r"시$|분$", dest):
        put("destination_text", dest)
    if re.search(r"돌아오|돌아와|원점|제자리", raw):
        put("round_trip", True)

    date, depart, ret, ambiguities = resolve_clock(raw, now)
    if date:
        put("date", date)
    if depart:
        put("depart_at", depart)
    if ret:
        put("return_by", ret)
        if slots.destination_text and re.match(r"\d", slots.destination_text):
            slots.destination_text = None
    m = re.search(r"(\d+|한|두|세|네|다섯|여섯)\s*(곳|군데|개)", raw)
    if m and _num(m.group(1)):
        put("stop_count", _num(m.group(1)))
    m = re.search(r"(\d+)\s*분\s*(?:씩|정도\s*씩|머물|체류)", raw)
    if m:
        put("stay_minutes", int(m.group(1)))
    if re.search(r"여유|동안|정도|시간 있", raw):
        free = _hours_minutes(raw)
        if free:
            put("free_minutes", free)

    changes, change_amb = ([], [])
    plan_like = re.search(PLAN_WORDS, raw)
    modify_like = has_trip and re.search(MODIFY_WORDS, raw)
    if modify_like and not re.search(r"짜\s*줘|새로|새 일정", raw):
        intent = "plan_modify"
        changes, change_amb = _changes(raw, themes)
        if (
            slots.destination_text
            and slots.destination_text in raw
            and not re.search(ROUTE_WORDS, raw)
        ):
            slots.destination_text = None
            sources.pop("destination_text", None)
    elif plan_like and (themes or slots.stop_count or slots.free_minutes):
        intent = "plan_create"
    elif slots.destination_text and re.search(ROUTE_WORDS + "|까지", raw):
        intent = "route"
    elif slots.facility:
        intent = "guide"
    elif named := _place_before(raw, r"(?:은|는|이|가)?\s*(?:설명|어떤\s*곳|뭐야)"):
        intent = "guide"
        put("place_query", named)
    elif themes and re.search(HERE + "|찾아|어디", raw):
        intent = "guide"
    elif unsupported:
        intent = "unsupported"
    else:
        intent = "chat"  # 인사·잡담·알아듣지 못한 말: 도구 없이 할 수 있는 일을 안내

    return ParseResult(
        intent=intent,
        slots=slots,
        ambiguities=ambiguities + change_amb,
        requested_changes=changes,
        unsupported_features=unsupported,
        slot_sources=sources,
        reference_time=now.isoformat(),
        parser="rules",
    )
