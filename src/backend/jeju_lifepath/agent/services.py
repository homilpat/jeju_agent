"""실제 도구 구현. 외부 자원(카카오, 경로 엔진, 공공데이터)은 AgentContext로 주입한다(시험에서 바꿔 끼움)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jeju_lifepath.agent.models import Policy, ToolError
from jeju_lifepath.agent.tools import (
    CompareRoutesIn,
    MatrixIn,
    NearbyIn,
    ResolveLocationIn,
    SearchPlacesIn,
    ToolSpec,
)
from jeju_lifepath.geo.geodesy import haversine_m

PHARMACY_CODE = "PM9"  # 카카오 분류 코드: 약국
NEARBY_LIMIT = 5  # api/app.py NEARBY_LIMIT과 같음
# 지명 검색 상위 몇 곳이 이 거리 안에 모두 모여 있으면 같은 장소로 본다.
# shortcut: 근거 없는 잠정값(시장·공원 한 곳의 부속시설 거리 정도), 오판 사례가 나오면 조정
SAME_PLACE_TOP, SAME_PLACE_M = 3, 500
TOILET_GEOCODE_MAX = 15  # shortcut: 근거 없는 상한(주소 검색 호출 수 제한), 느리면 조정
PUBLIC_SOURCES = {
    "wifi": "공공 와이파이 위치(제주데이터허브)",
    "police": "경찰관서 위치(생활안전지도)",
    "toilet": "공중화장실 정보(공공데이터포털)",
}


@dataclass
class AgentContext:
    kakao: object  # KakaoLocal 또는 시험용 가짜
    get_router: Callable[[], object | None]
    policy: Policy
    nearby: dict = field(default_factory=dict)  # api.app.load_nearby 결과
    toilets: list = field(default_factory=list)
    toilets_in: Callable | None = None
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
        places = ctx.kakao.keyword(a.query, near, a.radius_m)[: ctx.policy.max_candidates]
        return {"places": places, "source": "kakao", "checked_at": _now_iso()}

    def resolve_location(a: ResolveLocationIn) -> dict:
        at = ctx.kakao.address(a.text)
        if at and 125.9 <= at[0] <= 127.0 and 33.0 <= at[1] <= 34.05:
            return {"status": "found", "lon": at[0], "lat": at[1], "label": a.text}
        found = ctx.kakao.keyword(a.text)
        if not found:
            return {"status": "not_found"}
        exact = [p for p in found if p["name"] == a.text]
        top = found[: SAME_PLACE_TOP]
        # "동문시장" -> 시장·주차장·입구처럼 한곳에 모인 결과면 같은 장소로 본다(멀리 떨어진 동명이 아니면 묻지 않음)
        clustered = all(
            haversine_m(top[0]["lon"], top[0]["lat"], p["lon"], p["lat"]) <= SAME_PLACE_M
            for p in top
        )
        if len(found) == 1 or len(exact) == 1 or clustered:
            p = exact[0] if len(exact) == 1 else found[0]
            return {
                "status": "found",
                "lon": p["lon"],
                "lat": p["lat"],
                "label": p["name"],
                "ref": p["ref"],
            }
        return {"status": "ambiguous", "options": found[:3]}

    def find_nearby_facilities(a: NearbyIn) -> dict:
        at = (a.at.lon, a.at.lat)
        if a.kind == "pharmacy":
            found = ctx.kakao.category(PHARMACY_CODE, at, ctx.policy.search_radius_m)
            for p in found:
                p["distance_m"] = p["distance_m"] or round(haversine_m(*at, p["lon"], p["lat"]))
            return {"places": found[:NEARBY_LIMIT], "source": "kakao", "checked_at": _now_iso()}
        if a.kind == "toilet":
            region = ctx.kakao.region(*at)
            if region is None:
                return {"places": [], "source": "public", "outside": True}
            cands = ctx.toilets_in(ctx.toilets, region["city"], region["regions"])
            found = []
            for t in cands[:TOILET_GEOCODE_MAX]:
                spot = next((s for s in map(ctx.kakao.address, t["addresses"]) if s), None)
                if spot:
                    found.append(
                        {
                            "name": t["name"],
                            "address": t["addresses"][0],
                            "lon": spot[0],
                            "lat": spot[1],
                            "distance_m": round(haversine_m(*at, *spot)),
                            "notes": [
                                n
                                for n, ok in (
                                    ("장애인 화장실", t["accessible"]),
                                    ("기저귀 교환대", t["diaper"]),
                                    ("비상벨", t["bell"]),
                                )
                                if ok
                            ],
                            "hours": t["hours"],
                        }
                    )
            found.sort(key=lambda p: p["distance_m"])
            return {
                "places": found[:NEARBY_LIMIT],
                "source": "public",
                "partial": len(cands) > TOILET_GEOCODE_MAX,
            }
        points = ctx.nearby.get(a.kind, [])
        return {"places": ctx.nearest(points, a.at.lat, a.at.lon), "source": "public"}

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
                "약국·화장실·와이파이·경찰관서 가까운 곳",
                NearbyIn,
                find_nearby_facilities,
            ),
            ToolSpec(
                "compare_routes",
                "두 위치 사이 걷는 길 세 가지 비교",
                CompareRoutesIn,
                compare_routes,
            ),
            ToolSpec(
                "get_travel_matrix",
                "한 위치에서 여러 위치까지 실제 걷는 거리",
                MatrixIn,
                get_travel_matrix,
            ),
        )
    }
