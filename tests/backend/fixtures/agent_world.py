"""Agent 시험용 합성 세계: 작은 실제 보행망(격자) + 가짜 카카오. 실제 매장 정보가 아니다.

격자: 경도 126.50~126.51, 위도 33.50~33.51, 0.002도 간격 footway.
'계단빵집'은 계단(steps) 갈래길 끝에만 붙어 있어 유모차·휠체어로는 갈 수 없다.
"""

from __future__ import annotations

from datetime import datetime

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
]
AREAS = {"격자동": (CENTER["lon"], CENTER["lat"]), "격자마을": (LON0, LAT0)}


class FakeKakao:
    """카카오 로컬 REST 흉내. fail_queue에 ToolError를 넣으면 다음 호출에서 그 오류를 낸다."""

    def __init__(self, places=PLACES):
        self.places = places
        self.calls: list[str] = []
        self.fail_queue: list[ToolError] = []

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
            if "약국" in p[2] and haversine_m(near[0], near[1], p[3], p[4]) <= radius_m
        ]
        return [self._doc(p, near) for p in hits[:size]]

    def address(self, query):
        self._maybe_fail("address")
        return AREAS.get(query)

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
        toilets=[],
        toilets_in=lambda t, c, r: [],
        nearest=lambda pts, lat, lon: [],
        dataset_version="synthetic-grid",
    )
