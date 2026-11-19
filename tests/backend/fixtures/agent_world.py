"""Agent 시험용 합성 세계: 작은 실제 보행망(격자) + 가짜 카카오. 실제 매장 정보가 아니다.

격자: 경도 126.50~126.51, 위도 33.50~33.51, 0.002도 간격 footway.
'계단빵집'은 계단(steps) 갈래길 끝에만 붙어 있어 유모차·휠체어로는 갈 수 없다.
"""

from __future__ import annotations

from datetime import datetime

from shapely.geometry import box

from jeju_lifepath.agent.models import Policy, ToolError
from jeju_lifepath.agent.parse import SEOUL
from jeju_lifepath.agent.services import AgentContext
from jeju_lifepath.geo.geodesy import haversine_m
from jeju_lifepath.geo.osm import WalkNetwork, build_segments
from jeju_lifepath.routing.walk import WalkRouter

LON0, LAT0, STEP, N = 126.50, 33.50, 0.002, 6
CENTER = {"lon": LON0 + 0.004, "lat": LAT0 + 0.004}
NOW = datetime(2026, 11, 18, 13, 0, tzinfo=SEOUL)


def _grid_elements() -> list[dict]:
    els, wid = [], 1
    nid = lambda i, j: 1000 + i * 10 + j  # noqa: E731
    coords = {nid(i, j): (LON0 + i * STEP, LAT0 + j * STEP) for i in range(N) for j in range(N)}
    # 계단 갈래길: (1,1)에서 남서쪽으로 짧게
    coords[9001] = (LON0 + STEP - 0.0006, LAT0 + STEP - 0.0006)

    def way(nodes, **tags):
        nonlocal wid
        wid += 1
        return {
            "type": "way",
            "id": wid,
            "nodes": nodes,
            "tags": {"highway": "footway", **tags},
            "geometry": [{"lon": coords[n][0], "lat": coords[n][1]} for n in nodes],
        }

    for i in range(N):
        els.append(way([nid(i, j) for j in range(N)]))
        els.append(way([nid(j, i) for j in range(N)]))
    els.append(way([nid(1, 1), 9001], highway="steps"))
    return els


def make_router() -> WalkRouter:
    return WalkRouter(WalkNetwork(build_segments(_grid_elements())), None, None)


PLACES = [  # (id, 이름, 검색어들, 경도, 위도, 분류)
    ("k1", "계단빵집", ("빵집",), LON0 + STEP - 0.0006, LAT0 + STEP - 0.0006, "제과,베이커리"),
    ("k2", "격자빵집", ("빵집",), LON0 + 3 * STEP, LAT0 + 3 * STEP + 0.0002, "제과,베이커리"),
    ("k3", "모퉁이빵집", ("빵집",), LON0 + 4 * STEP, LAT0 + 1 * STEP, "제과,베이커리"),
    ("k4", "바람카페", ("카페",), LON0 + 2 * STEP + 0.0002, LAT0 + 4 * STEP, "카페"),
    ("k5", "돌담카페", ("카페",), LON0 + 5 * STEP, LAT0 + 5 * STEP, "카페"),
    ("k6", "격자해변", ("해수욕장",), LON0 + 5 * STEP, LAT0, "해수욕장"),
    ("k7", "골목식당", ("맛집",), LON0 + STEP, LAT0 + 3 * STEP, "한식"),
    ("k8", "중앙약국", ("약국",), LON0 + 2 * STEP, LAT0 + 2 * STEP + 0.0003, "약국"),
    ("k9", "한라공원", ("한라공원", "공원"), LON0 + 3 * STEP, LAT0 + 5 * STEP, "공원"),
    ("k10", "한라공원 주차장", ("한라공원",), LON0 + 3 * STEP + 0.0004, LAT0 + 5 * STEP, "주차장"),
    ("k11", "격자미술관", ("미술관",), LON0 + 2 * STEP, LAT0 + 3 * STEP, "미술관"),
    # 이름에 '빵'이 들어 있지만 빵집이 아닌 곳(종류 거르기 시험용)
    ("k12", "빵빵주차장", ("빵집",), LON0 + 2 * STEP, LAT0 + 2 * STEP, "주차장"),
    # 무장애 여행지(격자공원)와 같은 이름의 카카오 장소
    ("k13", "격자공원", ("격자공원",), LON0 + 0.004, LAT0 + 0.005, "공원"),
]
# 가짜 분류 경로(실제 카카오 분류 경로 모양)
CATEGORY_PATHS = {
    "제과,베이커리": "음식점 > 간식 > 제과,베이커리",
    "카페": "음식점 > 카페",
    "해수욕장": "여행 > 관광,명소 > 해수욕장,해변",
    "한식": "음식점 > 한식",
    "약국": "의료,건강 > 약국",
    "공원": "여행 > 공원",
    "주차장": "교통,수송 > 교통시설 > 주차장",
    "미술관": "문화,예술 > 문화시설 > 미술관",
}
# 가짜 카카오 분류 코드 -> 그 분류에 드는 검색어(PLACES의 검색어들)
CATEGORY_KEYS = {"PM9": "약국", "CT1": "미술관", "AT4": "공원"}
AREAS = {"격자동": (CENTER["lon"], CENTER["lat"]), "격자마을": (LON0, LAT0)}


class FakeKakao:
    """카카오 로컬 REST 흉내. fail_queue에 ToolError를 넣으면 다음 호출에서 그 오류를 낸다."""

    def __init__(self, places=PLACES):
        self.places = places
        self.calls: list[str] = []
        self.fail_queue: list[ToolError] = []
        self.truncate: set[str] = set()

    def _maybe_fail(self, name: str):
        self.calls.append(name)
        if self.fail_queue:
            raise self.fail_queue.pop(0)

    def _doc(self, p, near=None):
        pid, name, _keys, lon, lat, cat = p
        return {
            "ref": pid,
            "name": name,
            "category": cat,
            "category_path": CATEGORY_PATHS[cat].split(" > "),
            "address": f"제주특별자치도 제주시 격자동 {pid}",
            "lon": lon,
            "lat": lat,
            "url": f"https://place.map.kakao.com/{pid}",
            "distance_m": round(haversine_m(near[0], near[1], lon, lat)) if near else None,
        }

    def keyword(self, query, near=None, radius_m=None, size=15):
        self._maybe_fail("keyword")
        hits = [p for p in self.places if any(query in k or k in query for k in p[2])]
        if near and radius_m:
            hits = [p for p in hits if haversine_m(near[0], near[1], p[3], p[4]) <= radius_m]
            hits.sort(key=lambda p: haversine_m(near[0], near[1], p[3], p[4]))
        return [self._doc(p, near) for p in hits[:size]]

    def category(self, code, near, radius_m, size=15):
        self._maybe_fail("category")
        hits = [
            p
            for p in self.places
            if CATEGORY_KEYS[code] in p[2] and haversine_m(near[0], near[1], p[3], p[4]) <= radius_m
        ]
        return [self._doc(p, near) for p in hits[:size]]

    def search_all(self, *, query=None, code=None, near=None, radius_m=None, rect=None):
        """여러 쪽 검색 흉내. truncate에 검색어·분류 코드를 넣으면 '더 있음'으로 답한다."""
        self._maybe_fail("search_all")
        key = query or CATEGORY_KEYS[code]
        hits = [p for p in self.places if any(key in k or k in key for k in p[2])]
        if rect:
            w, s, e, n = rect
            hits = [p for p in hits if w <= p[3] <= e and s <= p[4] <= n]
        else:
            hits = [p for p in hits if haversine_m(near[0], near[1], p[3], p[4]) <= radius_m]
        return [self._doc(p, near) for p in hits[:45]], (query or code) in self.truncate

    def address(self, query):
        self._maybe_fail("address")
        return AREAS.get(query) or AREAS.get(query.split()[-1])  # "제주시 격자마을"도 찾음

    def region(self, lon, lat):
        self._maybe_fail("region")
        return {"city": "제주시", "regions": ["격자동"]}


def make_context(policy: Policy | None = None, kakao=None, router=None) -> AgentContext:
    r = router or make_router()
    return AgentContext(
        kakao=kakao or FakeKakao(),
        get_router=lambda: r,
        policy=policy or Policy.load(),
        nearby={"wifi": [], "police": []},
        # 격자 전체를 덮는 합성 동네 하나(실제 행정동 아님)
        areas={"격자동": box(LON0 - 0.001, LAT0 - 0.001, LON0 + 0.011, LAT0 + 0.011)},
        area_city={"격자동": "제주시"},
        toilets=[],
        toilets_in=lambda t, c, r: [],
        nearest=lambda pts, lat, lon: [],
        # 허브 자료 흉내(합성, 실제 시설 아님): 주소는 AREAS 이름이라 가짜 주소 검색이 찾는다
        addr_facilities={
            "night_pharmacy": [{"name": "별빛약국", "addresses": ["제주시 격자동"], "notes": ["밤 23:00까지"]}],
            "nursing_room": [{"name": "격자 주민센터", "addresses": ["제주시 격자마을"], "notes": ["가족수유실"]}],
            "emergency": [{"name": "격자병원", "addresses": ["제주시 격자동"],
                           "notes": ["지역응급의료센터", "전화 064-000-0000"]}],
        },
        olle=[{"course": "7코스", "name": "서귀포-월평", "length": "17.7km", "time": "5~6시간",
               "start": "한라공원", "end": "격자미술관"}],
        barrier_free=[{"name": "격자공원", "lat": CENTER["lat"] + 0.001, "lon": CENTER["lon"],
                       "address": "제주시 격자동", "points": [], "nearby": [], "doc": None,
                       "barrier_points": 1, "toilet_points": 1}],
        dataset_version="synthetic-grid",
    )
