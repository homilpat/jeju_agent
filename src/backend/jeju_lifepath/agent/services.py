"""실제 도구 구현. 외부 자원(카카오, 경로 엔진, 공공데이터)은 AgentContext로 주입한다(시험에서 바꿔 끼움)."""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import shapely

from jeju_lifepath.agent.models import Policy, ToolError
from jeju_lifepath.agent.parse import THEME_CATEGORY, is_kind
from jeju_lifepath.data.barrier_free import name_key
from jeju_lifepath.agent.tools import (
    AreaAccessIn,
    CompareRoutesIn,
    MatrixIn,
    BarrierFreeIn,
    NearbyIn,
    OlleIn,
    ResolveLocationIn,
    RouteStopsIn,
    SearchPlacesIn,
    ToolSpec,
)
from jeju_lifepath.geo.geodesy import haversine_m
from jeju_lifepath.routing.walk import MAX_SNAP_M

M_PER_DEG_LAT = 111_320  # 위도 1도의 길이(m, 구면 근사)
PHARMACY_CODE = "PM9"  # 카카오 분류 코드: 약국
NEARBY_LIMIT = 5  # api/app.py NEARBY_LIMIT과 같음
TOILET_GEOCODE_MAX = 15  # shortcut: 근거 없는 상한(주소 검색 호출 수 제한), 느리면 조정
# 허브 주소 자료(심야약국 6·응급의료기관 6·수유시설 85곳)를 한 번에 위치로 바꿀 최대 수.
# shortcut: 근거 없는 상한(수유시설 한 시 분량이 들어가게), 느리면 줄이기
ADDR_GEOCODE_MAX = 60
# 길에 들를 곳 카드 최대 6장(REQUIREMENTS §11.5 근거, §11.1-2)
CARD_MAX = 6
# 화장실 주소 -> 위치 바꾸기를 동시에 몇 개 부를지(도구 실행기 ToolRunner 스레드 수 8과 같게, 잠정)
GEOCODE_WORKERS = 8
# 좌표가 있는 공공데이터가 없는 시설은 카카오 장소 검색으로 찾는다(검색어·분류 코드)
KAKAO_FACILITY = {"pharmacy": {"code": PHARMACY_CODE}, "toilet": {"query": "공중화장실"}}
PUBLIC_SOURCES = {
    "wifi": "공공 와이파이 위치(제주데이터허브)",
    "police": "경찰관서 위치(생활안전지도)",
    "toilet": "공중화장실 정보(공공데이터포털)",
    "night_pharmacy": "공공심야약국 현황(제주데이터허브)",
    "nursing_room": "모유수유시설 설치현황(제주데이터허브)",
    "emergency": "응급의료기관 현황(제주데이터허브)",
}
OLLE_SOURCE = "올레코스 현황(제주데이터허브)"
BARRIER_FREE_SOURCE = "무장애여행정보(제주데이터허브·공공데이터포털)"


@dataclass
class AgentContext:
    kakao: object  # KakaoLocal 또는 시험용 가짜
    get_router: Callable[[], object | None]
    policy: Policy
    nearby: dict = field(default_factory=dict)  # api.app.load_nearby 결과
    # 읍면동 경계 {이름: shapely 도형(경도·위도)}. 지역 공백 분석의 범위(api.app.load_areas)
    areas: dict = field(default_factory=dict)
    area_city: dict = field(default_factory=dict)  # {읍면동 이름: 시(제주시·서귀포시)}
    toilets: list = field(default_factory=list)
    toilets_in: Callable | None = None
    # 제주데이터허브 주소 자료 {시설 종류: 화장실과 같은 모양}과 올레 코스(api.app.load_hub_links)
    addr_facilities: dict = field(default_factory=dict)
    olle: list = field(default_factory=list)
    barrier_free: list = field(default_factory=list)  # data.barrier_free.build 결과(장소별)
    nearest: Callable | None = None
    dataset_version: str = "local"
    # 게시 전에 여행 본문을 수동 플래너와 같은 규칙(app.Planner)으로 검사. 어긋나면 예외
    trip_validator: Callable[[dict], dict] | None = None
    # 이동 거리 캐시: (출발, 도착 목록, 이동 프로필, 자료 버전) -> 거리. 프로세스 메모리에만 둔다
    cache: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_tools(ctx: AgentContext) -> dict[str, ToolSpec]:
    def router():
        r = ctx.get_router()
        if r is None:
            raise ToolError("unavailable", "walk network missing")
        return r

    def search_places(a: SearchPlacesIn) -> dict:
        near = (a.near.lon, a.near.lat) if a.near else None
        if a.category and not (near and a.radius_m):
            raise ToolError("invalid_args", "category needs near and radius")
        if a.category:
            found = ctx.kakao.category(a.category, near, a.radius_m)
        elif a.only_kind and near and a.radius_m:
            # 거르면 줄어들므로 카카오가 주는 끝까지(최대 45곳) 받아 맞는 곳만, 거리순 그대로
            found, _ = ctx.kakao.search_all(query=a.query, near=near, radius_m=a.radius_m)
            found = [p for p in found if is_kind(p, a.query)]
        else:
            found = ctx.kakao.keyword(a.query, near, a.radius_m)
        places = found[: ctx.policy.max_candidates]
        return {"places": places, "source": "kakao", "checked_at": _now_iso()}

    def resolve_location(a: ResolveLocationIn) -> dict:
        at = ctx.kakao.address(a.text)
        if at and 125.9 <= at[0] <= 127.0 and 33.0 <= at[1] <= 34.05:
            return {"status": "found", "lon": at[0], "lat": at[1], "label": a.text}
        found = ctx.kakao.keyword(a.text)
        if not found:
            return {"status": "not_found"}
        exact = [p for p in found if p["name"] == a.text]
        if len(found) == 1 or len(exact) == 1:
            p = exact[0] if len(exact) == 1 else found[0]
            return {
                "status": "found",
                "lon": p["lon"],
                "lat": p["lat"],
                "label": p["name"],
                "ref": p["ref"],
            }
        # 이름이 딱 맞는 곳이 하나가 아니면 고르게 한다(시장 입구·주차장처럼 붙어 있는 곳도 묻는다)
        return {"status": "ambiguous", "options": found[:5]}

    def find_nearby_facilities(a: NearbyIn) -> dict:
        at = (a.at.lon, a.at.lat)
        if a.kind == "pharmacy":
            found = ctx.kakao.category(PHARMACY_CODE, at, ctx.policy.search_radius_m)
            for p in found:
                p["distance_m"] = p["distance_m"] or round(haversine_m(*at, p["lon"], p["lat"]))
            return {"places": found[:NEARBY_LIMIT], "source": "kakao", "checked_at": _now_iso()}
        if a.kind == "toilet" or a.kind in ctx.addr_facilities:
            return nearby_by_address(a.kind, at)
        points = ctx.nearby.get(a.kind, [])
        return {"places": ctx.nearest(points, a.at.lat, a.at.lon), "source": "public"}

    def geocode(rows: list[dict], tool: str) -> list[tuple[dict, tuple | None]]:
        """주소 -> 위치(카카오 주소 검색, 이번 계산 동안만. 저장 안 함). 도구 시간 안에 못 바꾼 곳은 None."""
        until = time.monotonic() + ctx.policy.timeout_for(tool)

        def locate(x):
            if time.monotonic() >= until:
                return x, None
            return x, next((s for s in map(ctx.kakao.address, x["addresses"]) if s), None)

        with ThreadPoolExecutor(max_workers=GEOCODE_WORKERS) as pool:
            return list(pool.map(locate, rows))

    def nearby_by_address(kind: str, at: tuple) -> dict:
        """주소만 있는 공공 자료(화장실, 허브 심야약국·수유시설·응급의료기관) 중 가까운 곳."""
        region = ctx.kakao.region(*at)
        if region is None:
            return {"places": [], "source": "public", "outside": True}
        if kind == "toilet":
            cands, cap = ctx.toilets_in(ctx.toilets, region["city"], region["regions"]), TOILET_GEOCODE_MAX
        else:
            rows, cap = ctx.addr_facilities.get(kind, []), ADDR_GEOCODE_MAX
            # 섬 전체 몇 곳뿐인 자료(심야약국·응급실)는 다 보고, 많으면 같은 시만 본다
            cands = rows if len(rows) <= cap else [
                x for x in rows if any(region["city"] in ad for ad in x["addresses"])]
        found = []
        for x, spot in geocode(cands[:cap], "find_nearby_facilities"):
            if spot is None:
                continue
            notes = x.get("notes") or [n for n, ok in (
                ("장애인 화장실", x.get("accessible")), ("기저귀 교환대", x.get("diaper")),
                ("비상벨", x.get("bell"))) if ok]
            found.append({"name": x["name"], "address": x["addresses"][0], "lon": spot[0], "lat": spot[1],
                          "distance_m": round(haversine_m(*at, *spot)), "notes": notes,
                          "hours": x.get("hours", "")})
        found.sort(key=lambda p: p["distance_m"])
        return {"places": found[:NEARBY_LIMIT], "source": "public", "partial": len(cands) > cap}

    def olle_courses(a: OlleIn) -> dict:
        """올레 코스(허브 1236) + 시작·끝 지점 위치(카카오 장소 검색, 이번 계산 동안만)."""
        def key(text: str) -> str:
            return text.replace("코스", "").replace(" ", "")

        if a.course:
            picked = [c for c in ctx.olle if key(c["course"]) == key(a.course)]
        else:
            picked = list(ctx.olle)

        until = time.monotonic() + ctx.policy.timeout_for("olle_courses") * 0.8  # 재시도 전에 끝나게

        def spot(name: str | None):
            if not name or time.monotonic() >= until:  # 시간을 넘기면 더 찾지 않는다(그 코스는 빠짐)
                return None
            hits = ctx.kakao.keyword(name)
            return (hits[0]["lon"], hits[0]["lat"]) if hits else None

        with ThreadPoolExecutor(max_workers=GEOCODE_WORKERS) as pool:
            starts = list(pool.map(lambda c: spot(c["start"]), picked))
        out = []
        for c, st in zip(picked, starts, strict=True):
            row = {**c, "start_at": st, "end_at": None, "distance_m": None}
            if a.near and st:
                row["distance_m"] = round(haversine_m(a.near.lon, a.near.lat, *st))
            out.append(row)
        if not a.course:  # 가까운 코스 3개(시작점 기준)
            out = sorted((c for c in out if c["distance_m"] is not None), key=lambda c: c["distance_m"])[:3]
        else:
            for c in out:
                c["end_at"] = spot(c["end"]) if c["end"] and c["end"] != c["start"] else None
        return {"courses": out, "source": "public"}

    def barrier_free_places(a: BarrierFreeIn) -> dict:
        """무장애 여행지(허브·포털 무장애여행정보). 이름이면 그 장소, 아니면 가까운 5곳."""
        if a.name:
            want = name_key(a.name)
            hits = [p for p in ctx.barrier_free if name_key(p["name"]) == want] or [
                p for p in ctx.barrier_free if want in name_key(p["name"]) or name_key(p["name"]) in want
            ]
            return {"places": hits[:1], "detail": True, "source": "public"}
        if a.near is None:
            raise ToolError("invalid_args", "name or near")
        ranked = sorted(
            ({**p, "distance_m": round(haversine_m(a.near.lon, a.near.lat, p["lon"], p["lat"]))}
             for p in ctx.barrier_free),
            key=lambda p: p["distance_m"],
        )
        return {"places": ranked[:NEARBY_LIMIT], "detail": False, "source": "public"}

    def compare_routes(a: CompareRoutesIn) -> dict:
        from jeju_lifepath.presentation.route import render_plan  # noqa: PLC0415

        plan = router().plan(
            (a.origin.lon, a.origin.lat),
            (a.destination.lon, a.destination.lat),
            a.mobility,
            prefer_cctv=a.prefer_cctv,
        )
        shown = render_plan(plan)
        return {**shown, "found": any(r["found"] for r in shown["routes"])}

    def public_toilets(rows: list[dict], tool: str, **where) -> tuple[list[dict], bool, str, int]:
        """공중화장실 공공데이터(주소만 있음)를 이번 계산 동안만 카카오 주소 검색으로 위치로 바꾼다(저장 안 함).
        범위에 걸친 시(제주시·서귀포시)의 화장실만 바꾸고, 범위(사각형·원) 밖은 뺀다.
        반환 (장소들, 시간 안에 다 못 바꿈, 출처, 주소로 위치를 못 찾은 수)."""
        if "rect" in where:
            w, s_, e, n = where["rect"]
            reach = shapely.box(w, s_, e, n)
            inside = lambda lon, lat: w <= lon <= e and s_ <= lat <= n  # noqa: E731
        else:
            (lon0, lat0), r = where["near"], where["radius_m"]
            # 범위에 걸친 동네를 고르는 데만 쓰는 사각형(원을 덮음). 실제 범위 판정은 걷는 거리가 아닌 직선거리 r
            dlat = r / M_PER_DEG_LAT
            dlon = dlat / max(0.1, math.cos(math.radians(lat0)))
            reach = shapely.box(lon0 - dlon, lat0 - dlat, lon0 + dlon, lat0 + dlat)
            inside = lambda lon, lat: haversine_m(lon0, lat0, lon, lat) <= r  # noqa: E731
        # 범위에 걸친 읍면동의 시(경계 자료가 없으면 두 시 모두)
        cities = {ctx.area_city[n] for n, g in ctx.areas.items() if g.intersects(reach)} or {
            "제주시", "서귀포시"}
        cands = [t for t in rows if any(c in a for c in cities for a in t["addresses"])]
        until = time.monotonic() + ctx.policy.timeout_for(tool)

        def locate(t):
            if time.monotonic() >= until:
                return t, None, True
            return t, next((x for x in map(ctx.kakao.address, t["addresses"]) if x), None), False

        found, unlocated, late = [], 0, False
        with ThreadPoolExecutor(max_workers=GEOCODE_WORKERS) as pool:
            for i, (t, spot, too_late) in enumerate(pool.map(locate, cands)):
                late |= too_late
                if spot is None:
                    unlocated += not too_late
                    continue
                if inside(*spot):
                    found.append({"ref": f"toilet:{i}", "name": t["name"], "address": t["addresses"][0],
                                  "lon": spot[0], "lat": spot[1], "url": None})
        return found, late, "public", unlocated

    def facility_points(kind: str | None, theme: str | None, tool: str, **where):
        """시설 후보 (장소들, 잘림, 출처, 위치 못 찾은 수). 공공데이터가 있으면 그것, 없으면 카카오(최대 45곳)."""
        if kind == "toilet" and ctx.toilets:
            return public_toilets(ctx.toilets, tool, **where)
        if kind in ctx.addr_facilities or kind in ("night_pharmacy", "nursing_room", "emergency"):
            rows = ctx.addr_facilities.get(kind, [])
            return public_toilets(rows, tool, **where) if rows else ([], False, "public", 0)
        if kind in ("wifi", "police"):
            pts = [{**p, "ref": f"{kind}:{i}"} for i, p in enumerate(ctx.nearby.get(kind, []))]
            if "rect" in where:
                w, s, e, n = where["rect"]
                pts = [p for p in pts if w <= p["lon"] <= e and s <= p["lat"] <= n]
            return pts, False, "public", 0
        what = KAKAO_FACILITY.get(kind) or (
            {"code": THEME_CATEGORY[theme]} if theme in THEME_CATEGORY else {"query": theme}
        )
        if "rect" not in where:
            found, truncated = ctx.kakao.search_all(**what, **where)
            if theme:
                found = [p for p in found if is_kind(p, theme)]
            return found, truncated, "kakao", 0
        # 사각형 검색은 카카오가 45곳에서 자르면 4칸으로 나눠 칸마다 다시 찾는다(빠짐없이 세려고).
        # 이 도구에 주어진 시간(configs/agent_policy.json) 안에 다 못 나누면 잘림으로 알린다
        until = time.monotonic() + ctx.policy.timeout_for("analyze_area_access")
        todo, found, seen = [where["rect"]], [], set()
        while todo:
            if time.monotonic() >= until:
                return found, True, "kakao", 0
            w, s, e, n = todo.pop()
            part, truncated = ctx.kakao.search_all(**what, rect=(w, s, e, n))
            if truncated:
                mx, my = (w + e) / 2, (s + n) / 2
                todo += [(w, s, mx, my), (mx, s, e, my), (w, my, mx, n), (mx, my, e, n)]
                continue
            for x in part:
                if theme and not is_kind(x, theme):
                    continue
                if x["ref"] not in seen:
                    seen.add(x["ref"])
                    found.append(x)
        return found, False, "kakao", 0

    def find_route_stops(a: RouteStopsIn) -> dict:
        """§11.1 다음 후보 규칙을 출발지 한 단계에 적용: (a) 그곳->도착지 < 출발지->도착지(되돌아가지 않음)
        (c) 우회 거리 오름차순, 동률은 이름순. 우회 상한 없음(§11.5). 우회 = 출발->후보 + 후보->도착 - 직행.
        거리는 그래프 탐색 2번(출발지에서, 도착지에서)으로 모든 후보를 한꺼번에 구한다(§11.4)."""
        o, d = (a.origin.lon, a.origin.lat), (a.destination.lon, a.destination.lat)
        direct = router().distances_from(d, [o], a.mobility)[0]
        if direct is None:
            return {"status": "no_route"}
        # (a)를 지키는 곳은 길 위에서 도착지까지 직행보다 가깝다. 시설·도착지가 길에서 떨어진 거리(스냅,
        # 최대 MAX_SNAP_M)까지 더하면 직선거리 상한 = 직행 + 스냅 상한 2번(카카오 반경 상한 20km)
        cands, truncated, source, unlocated = facility_points(
            a.facility, a.theme, "find_route_stops", near=d, radius_m=int(direct + 2 * MAX_SNAP_M)
        )
        pts = [(c["lon"], c["lat"]) for c in cands]
        to_end = router().distances_from(d, pts, a.mobility)
        from_start = router().distances_from(o, pts, a.mobility)
        ranked, unreachable = [], 0
        for c, e, s in zip(cands, to_end, from_start, strict=True):
            if e is None or s is None:
                unreachable += 1
                continue
            if e >= direct:
                continue
            ranked.append({**c, "detour_m": s + e - direct, "from_start_m": s})
        ranked.sort(key=lambda c: (c["detour_m"], c["name"]))
        return {
            "status": "ok",
            "stops": ranked[:CARD_MAX],
            "direct_m": direct,
            "unreachable": unreachable,
            "truncated": truncated,
            "unlocated": unlocated,
            "source": source,
            "checked_at": _now_iso(),
        }

    def analyze_area_access(a: AreaAccessIn) -> dict:
        area = ctx.areas.get(a.area)
        if area is None:
            raise ToolError("invalid_args", "unknown area")
        until = time.monotonic() + ctx.policy.timeout_for("analyze_area_access")
        found, truncated, source, unlocated = facility_points(
            a.facility, None, "analyze_area_access", rect=area.bounds
        )
        # 동 경계 안 시설만 센다(경계 밖 시설은 셈에서 빠짐을 화면에 밝힌다)
        found = [f for f in found if shapely.contains_xy(area, f["lon"], f["lat"])]
        base = {"facilities": found, "truncated": truncated, "source": source,
                "unlocated": unlocated, "checked_at": _now_iso()}
        if truncated:  # 다 세지 못한 시설로 '먼 곳'을 말하면 틀린 공백이 된다: 계산하지 않음
            return {**base, "status": "truncated"}
        try:
            points = router().nearest_source_distances(
                [(f["lon"], f["lat"]) for f in found], area, a.mobility, until=until
            )
        except TimeoutError:
            raise ToolError("timeout", "area access") from None
        if points is None:
            return {**base, "status": "unsupported_mobility"}
        return {**base, "status": "ok", "points": points}

    def get_travel_matrix(a: MatrixIn) -> dict:
        key = (
            (round(a.source.lon, 6), round(a.source.lat, 6)),
            tuple((round(t.lon, 6), round(t.lat, 6)) for t in a.targets),
            a.mobility.value,
            ctx.dataset_version,
        )
        with ctx.lock:
            if key in ctx.cache:
                return {"metres": ctx.cache[key], "cached": True}
        metres = router().distances_from(
            (a.source.lon, a.source.lat), [(t.lon, t.lat) for t in a.targets], a.mobility
        )
        with ctx.lock:
            ctx.cache[key] = metres
        return {"metres": metres, "cached": False}

    return {
        s.name: s
        for s in (
            ToolSpec(
                "search_places",
                "카카오 장소 검색(키워드, 위치 주변)",
                SearchPlacesIn,
                search_places,
            ),
            ToolSpec(
                "resolve_location",
                "지명 -> 위치. 여러 곳이면 후보",
                ResolveLocationIn,
                resolve_location,
            ),
            ToolSpec(
                "find_nearby_facilities",
                "약국·화장실·와이파이·경찰관서·심야약국·수유실·응급실 가까운 곳",
                NearbyIn,
                find_nearby_facilities,
            ),
            ToolSpec(
                "barrier_free_places",
                "무장애 여행지(휠체어·유모차 접근성 조사 자료) 가까운 곳 또는 한 곳 상세",
                BarrierFreeIn,
                barrier_free_places,
            ),
            ToolSpec(
                "olle_courses",
                "제주 올레 코스 안내(코스 번호 또는 가까운 코스)",
                OlleIn,
                olle_courses,
            ),
            ToolSpec(
                "compare_routes",
                "두 위치 사이 걷는 길 세 가지 비교",
                CompareRoutesIn,
                compare_routes,
            ),
            ToolSpec(
                "find_route_stops",
                "두 곳 사이 걷는 길에 들를 시설 후보와 우회 거리",
                RouteStopsIn,
                find_route_stops,
            ),
            ToolSpec(
                "analyze_area_access",
                "한 지역 길에서 가장 가까운 시설까지 걷는 거리",
                AreaAccessIn,
                analyze_area_access,
            ),
            ToolSpec(
                "get_travel_matrix",
                "한 위치에서 여러 위치까지 실제 걷는 거리",
                MatrixIn,
                get_travel_matrix,
            ),
        )
    }
