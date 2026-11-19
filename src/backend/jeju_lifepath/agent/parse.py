"""자연어 요청 -> ParseResult (규칙 기반). LLM이 없거나 실패했을 때만 쓴다(AG-204).
LLM 모드에서 아래 낱말 목록은 프롬프트의 참고 예시로만 쓰인다(llm.SYSTEM_PROMPT).

시각은 서버 시각, Asia/Seoul 기준(AG-105).
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
    # 넓은 말은 카카오 분류(아래 THEME_CATEGORY)로 찾는다
    "문화예술": "문화시설",
    "문화": "문화시설",
    "예술": "문화시설",
    "관광지": "관광명소",
    "명소": "관광명소",
}
# 한 말이 여러 종류를 뜻할 때(사용자 결정 2026-11-18: 자연 = 오름·해변·숲길)
THEME_GROUPS = {"자연": ["오름", "해수욕장", "숲길"]}
# 카카오 로컬 분류 그룹 코드로 찾는 종류(카카오 개발자 문서의 category_group_code)
THEME_CATEGORY = {"문화시설": "CT1", "관광명소": "AT4"}
# 키워드로 찾는 종류는 이름만 맞는 다른 곳(주차장·학원·모텔 등)이 섞인다. 일정에 넣을 후보는
# 카카오 분류 경로가 아래로 시작하는 곳만 쓴다. 값은 2026-11-18 제주시청·서귀포·애월 둘레 실제
# 카카오 키워드 검색 결과의 분류 경로에서 가져왔다(docs/development/open-decisions.md).
THEME_PATHS = {
    "빵집": [("음식점", "간식", "제과,베이커리")],
    "카페": [("음식점", "카페")],
    "해수욕장": [("여행", "관광,명소", "해수욕장,해변")],
    "오름": [("여행", "관광,명소", "오름")],
    "숲길": [("여행", "관광,명소", "도보여행"), ("여행", "관광,명소", "숲")],
    "맛집": [("음식점",)],
    "시장": [("가정,생활", "시장")],
    "박물관": [("문화,예술", "문화시설", "박물관")],
    "미술관": [("문화,예술", "문화시설", "미술관")],
    "공원": [("여행", "공원")],
}


def themes_in(text: str, names: list[str | None] = ()) -> list[str]:
    """말 속 장소 종류들. 지명·가게 이름(names) 안의 글자는 빼고 본다("매일올레시장"의 '시장'은 종류가 아님)."""
    for name in names:
        if name:
            text = text.replace(name, " ")
    found: list[str] = []
    for word in [*THEMES, *THEME_GROUPS]:
        if word in text:
            found += [k for k in theme_keys(word) if k not in found]
    return found


def is_kind(place: dict, theme: str) -> bool:
    """카카오 분류 경로가 그 종류에 맞는지. 맛집은 카페를 뺀 음식점. 정해 두지 않은 종류는 거르지 않음."""
    rules = THEME_PATHS.get(theme)
    if rules is None:
        return True
    path = tuple(place.get("category_path") or ())
    if theme == "맛집" and path[:2] == ("음식점", "카페"):
        return False
    return any(path[: len(r)] == r for r in rules)


def theme_keys(word: str) -> list[str]:
    """사용자 말 하나 -> 찾을 종류들. 모르는 말이면 빈 목록."""
    if word in THEME_GROUPS:
        return list(THEME_GROUPS[word])
    return [THEMES[word]] if word in THEMES else []
FACILITIES = {  # 긴 말을 먼저 본다("심야약국"이 "약국"으로 잡히지 않게)
    "심야약국": "night_pharmacy",
    "수유실": "nursing_room",
    "응급실": "emergency",
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
# "가는 길에 약국", "가다가 들를 화장실": 길 위에서 들를 곳
ON_ROUTE_WORDS = r"가는\s*길에|길에|가다가|가는\s*중|중간에|도중"
# "약국 부족한 곳", "화장실 공백": 지역의 시설 접근 공백
GAP_WORDS = r"공백|부족|모자라|사각지대|멀리\s*있는\s*곳|접근성"
SAVE_WORDS = r"저장"
# 여러 날 요청에서 날을 가르는 말(순서대로 1일차, 2일차…). "마지막"은 마지막 날
DAY_MARK = (
    r"첫\s*날|첫째\s*날|하루는|1\s*일\s*차|이튿날|둘째\s*날|2\s*일\s*차|셋째\s*날|3\s*일\s*차"
    r"|넷째\s*날|4\s*일\s*차|마지막\s*날|마지막에는|마지막엔|마지막에"
)
NEXT_DAY = r"다음\s*날|\d\s*일\s*차|이어서"


def day_themes_in(text: str, names: list[str | None] = ()) -> list[list[str]]:
    """날 표시로 나눈 조각마다 종류. 종류가 있는 날이 둘 이상일 때만(아니면 빈 목록)."""
    cuts = [m.start() for m in re.finditer(DAY_MARK, text)]
    if len(cuts) < 2:  # noqa: PLR2004 - 날이 둘 이상이어야 여러 날
        return []
    parts = [text[a:b] for a, b in zip(cuts, [*cuts[1:], len(text)], strict=True)]
    days = [themes_in(part, names) for part in parts]
    days = [d for d in days if d]
    return days if len(days) >= 2 else []  # noqa: PLR2004


# "애월읍", "삼양1동", "한림리" 같은 지역 이름(공백 분석의 지역)
AREA_NAME = r"([가-힣0-9]+(?:읍|면|동|리))(?=\s|$|에|의)"


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
    if word in NOT_PLACE or word in THEMES or word in THEME_GROUPS or word in FACILITIES:
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
    if "유모차" in raw:
        put("mobility", MobilityProfile.STROLLER)
    elif "휠체어" in raw:
        put("mobility", MobilityProfile.WHEELCHAIR)
    elif "그냥 걸어서" in raw:  # "휠체어나 유모차를 쓰니?"에 '아니, 걸어서' 버튼 답: 이동 조건을 보통으로
        put("mobility", MobilityProfile.STANDARD)
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
    themes = themes_in(raw, [slots.location_text, slots.destination_text])
    if themes:
        put("themes", themes)
    days = day_themes_in(raw, [slots.location_text, slots.destination_text])
    if days:
        put("day_themes", days)
    if re.search(NEXT_DAY, raw) and not themes:
        put("next_day", True)

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

    # 덮어쓰기는 부정("덮어쓰지 말고")이 없을 때만. 둘 다 말하거나 둘 다 없으면 정하지 않고 묻는다
    over = re.search(r"덮어", raw) and not re.search(r"덮어\s*쓰지\s*(말|마|않)|덮지\s*(말|마|않)", raw)
    new = re.search(r"새\s*여행|새로", raw)
    if over and not new:
        put("save_mode", "overwrite")
    elif new and not over:
        put("save_mode", "new")
    if re.search(GAP_WORDS, raw) and not slots.location_text:
        # "애월읍 약국 부족", "격자마을 약국 공백": 읍면동 이름, 없으면 시설 이름 바로 앞 낱말
        area = re.search(AREA_NAME, raw)
        word = area.group(1) if area else _place_before(raw, "|".join(FACILITIES))
        if word and word not in NOT_PLACE:
            put("location_text", word)

    changes, change_amb = ([], [])
    plan_like = re.search(PLAN_WORDS, raw)
    modify_like = has_trip and re.search(MODIFY_WORDS, raw)
    if re.search(SAVE_WORDS, raw) and not re.search(PLAN_WORDS + "|" + ROUTE_WORDS, raw):
        intent = "save_trip"
    elif (slots.facility or themes) and re.search(GAP_WORDS, raw):
        intent = "area_gap"
    elif (slots.facility or themes) and slots.destination_text and re.search(ON_ROUTE_WORDS, raw):
        intent = "route_poi"
    elif modify_like and not re.search(r"짜\s*줘|새로|새 일정", raw):
        intent = "plan_modify"
        changes, change_amb = _changes(raw, themes)
        if (
            slots.destination_text
            and slots.destination_text in raw
            and not re.search(ROUTE_WORDS, raw)
        ):
            slots.destination_text = None
            sources.pop("destination_text", None)
    elif plan_like and (themes or slots.stop_count or slots.free_minutes or slots.next_day):
        intent = "plan_create"
    elif slots.destination_text and re.search(ROUTE_WORDS + "|까지", raw):
        intent = "route"
    elif "올레" in raw:
        intent = "olle"
    elif "무장애" in raw:
        intent = "barrier_free"
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
