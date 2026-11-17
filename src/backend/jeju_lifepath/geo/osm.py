"""OSM 도로(Overpass JSON) -> 보행 구간망. 스냅·잘라내기에 쓰는 공간 색인 포함.

전체 제주망(약 4만 개 도로)을 RoadEdge로 한꺼번에 만들지 않는다. 가벼운 구간 목록과
길이 그래프만 들고 있다가, 요청마다 출발·도착 주변만 잘라 RoadEdge로 만든다(routing/walk.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import networkx as nx
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, Point

from jeju_lifepath.geo.geodesy import haversine_m

SOURCE_ID = "osm:jeju_highways"
# 보행 금지가 분명한 도로 종류
# (scripts/check_osm_coverage.py 와 같은 기준, 도로교통법 제63조 고속도로)
NOT_WALK = frozenset(
    {
        "motorway",
        "motorway_link",
        "construction",
        "proposed",
        "raceway",
        "platform",
        "corridor",
        "bus_stop",
    }
)
FOOT_YES = frozenset({"yes", "designated", "permissive"})
# 미터 단위 계산용: 한국 통합좌표계(UTM-K)
_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)


def is_walkable(tags: dict[str, str]) -> bool:
    """foot=yes 류가 있으면 일반 접근 제한(access=private 등)보다 우선한다(OSM 태그 관례).
    shortcut: 장벽 노드(barrier)·시간제 제한·한쪽 방향 보행 제한은 반영하지 않음,
    실측 문제 생기면 추가."""
    highway = tags.get("highway")
    if highway in NOT_WALK or tags.get("foot") == "no" or tags.get("motorroad") == "yes":
        return False
    if tags.get("foot") in FOOT_YES:
        return True
    return tags.get("access") not in ("private", "no")


@dataclass(frozen=True)
class Segment:
    seg_id: str
    u: str
    v: str
    coords: tuple[tuple[float, float], ...]  # (lon, lat)
    length_m: float


def build_segments(elements: list[dict]) -> list[Segment]:
    """보행 가능한 도로를 교차점(두 도로 이상이 같은 노드를 쓰는 곳)과 끝점에서 자른다.
    OSM에서 도로는 같은 노드 ID를 써야 이어지므로, 다리·지하도가 좌표만 겹쳐서 붙는 일은 없다."""
    ways = [
        w
        for w in elements
        if w.get("type") == "way"
        and len(w.get("nodes", [])) >= 2  # noqa: PLR2004 - 선은 점이 둘 이상
        and is_walkable(w.get("tags", {}))
    ]
    use: dict[int, int] = {}
    for w in ways:
        for n in w["nodes"]:
            use[n] = use.get(n, 0) + 1
    segments = []
    for w in ways:
        nodes = w["nodes"]
        pts = [(p["lon"], p["lat"]) for p in w["geometry"]]
        start, k = 0, 0
        for i in range(1, len(nodes)):
            if i == len(nodes) - 1 or use[nodes[i]] > 1:
                piece = tuple(pts[start : i + 1])
                length = sum(haversine_m(*a, *b) for a, b in pairwise(piece))
                if length > 0 and nodes[start] != nodes[i]:
                    segments.append(
                        Segment(
                            f"osm{w['id']}_{k}", str(nodes[start]), str(nodes[i]), piece, length
                        )
                    )
                    k += 1
                start = i
    return segments


@dataclass(frozen=True)
class Snap:
    segment: Segment
    distance_m: float
    fraction: float  # 0 = u 쪽 끝, 1 = v 쪽 끝 (구간 길이 비율)


class WalkNetwork:
    def __init__(self, segments: list[Segment]):
        self.segments = segments
        self.by_id = {s.seg_id: s for s in segments}
        self.graph = nx.Graph()
        for s in segments:
            # 같은 두 노드 사이 평행 구간은 짧은 것만 길이 그래프에 둔다
            # (기준 거리 계산용, 실제 탐색은 잘라낸 망에서 전부 씀)
            if not self.graph.has_edge(s.u, s.v) or self.graph[s.u][s.v]["length"] > s.length_m:
                self.graph.add_edge(s.u, s.v, length=s.length_m)
        self.node_lonlat = {}
        for s in segments:
            self.node_lonlat[s.u] = s.coords[0]
            self.node_lonlat[s.v] = s.coords[-1]
        self._lines_m = [self._to_m_line(s.coords) for s in segments]
        self._tree = shapely.STRtree(self._lines_m)

    @staticmethod
    def _to_m_line(coords) -> LineString:
        xs, ys = _TO_M.transform([c[0] for c in coords], [c[1] for c in coords])
        return LineString(zip(xs, ys, strict=True))

    @classmethod
    def from_file(cls, path: Path) -> WalkNetwork:
        return cls(build_segments(json.loads(path.read_text(encoding="utf-8"))["elements"]))

    def snap(self, lon: float, lat: float) -> Snap | None:
        """가장 가까운 보행 구간 위의 점(미터 좌표계에서 계산)."""
        if not self.segments:
            return None
        p = Point(*_TO_M.transform(lon, lat))
        i = int(self._tree.nearest(p))
        line = self._lines_m[i]
        return Snap(self.segments[i], line.distance(p), line.project(p, normalized=True))
