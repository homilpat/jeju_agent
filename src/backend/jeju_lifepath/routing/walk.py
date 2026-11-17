"""좌표 두 개 -> 실제 제주 보행망에서 세 경로(최단·균형·안전환경) 계산.

1) 두 좌표를 가장 가까운 보행 구간에 붙이고(스냅), 그 구간을 잘라 출발·도착 노드를 만든다.
2) 전체망에서 최단 거리 L0를 구한다. 우회 상한 r 안의 경로는 모두 "출발·도착을 초점으로 하고
   두 초점까지 거리 합이 L0(1+r) 이하인 타원" 안에 있으므로, 그 안의 구간만 잘라 계산한다
   (정확한 잘라내기).
3) 잘라낸 구간을 RoadEdge로 만들고 사고다발 구역 지표를 붙여 compare_routes() -> validate_route().
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import networkx as nx
import shapely
from shapely.geometry import LineString, box, shape
from shapely.ops import substring

from jeju_lifepath.geo.geodesy import haversine_m
from jeju_lifepath.geo.graph import PedestrianGraph
from jeju_lifepath.geo.osm import _TO_M, SOURCE_ID, Segment, Snap, WalkNetwork
from jeju_lifepath.routing.optimizer import compare_routes
from jeju_lifepath.schemas import MobilityProfile, RoadEdge, RouteRequest, RouteResult, RouteStatus
from jeju_lifepath.schemas.common import RoutePreference
from jeju_lifepath.schemas.request import DETOUR_RATIO_POLICY_MAX
from jeju_lifepath.validation.route import validate_route

log = logging.getLogger(__name__)

ORIGIN, DESTINATION = "출발", "도착"
ZONES_SOURCE_ID = "accident_zones"
# 보행 경로라 자전거 사고 다발구역은 빼고,
# 나머지(전체·보행노인·보행어린이·스쿨존어린이)는 모두 보행자에게 해당
NON_PEDESTRIAN_KINDS = frozenset({"자전거"})
# 스냅 지점이 구간 끝에서 이 거리 안이면 새 노드를 만들지 않고 끝 노드를 그대로 쓴다
# (길이 0 구간 방지용, 결과에 영향 거의 없음)
END_MERGE_M = 1.0

MSG_NOT_NEAR_ROAD = "출발지나 도착지 근처에서 걸을 수 있는 길을 찾지 못했습니다."
MSG_TOO_CLOSE = "출발지와 도착지가 너무 가까워 경로를 따로 안내하지 않습니다."
MSG_NOT_CONNECTED = (
    "두 장소를 잇는 걷는 길을 찾지 못했습니다. 섬이 다르거나 길 자료가 끊겨 있을 수 있습니다."
)
MSG_CHECK_FAILED = "경로를 확인하는 중 문제가 있어 이 경로는 보여드리지 않습니다."


@dataclass
class PlannedRoute:
    result: RouteResult
    line: list[tuple[float, float]] = field(default_factory=list)  # (lat, lon) 지도용


@dataclass
class RoutePlan:
    routes: dict[RoutePreference, PlannedRoute]
    origin_snap_m: float | None = None
    destination_snap_m: float | None = None


def _unavailable(msg: str) -> dict[RoutePreference, PlannedRoute]:
    r = RouteResult(status=RouteStatus.DATA_UNAVAILABLE, caveats=(msg,))
    return {
        p: PlannedRoute(r)
        for p in (RoutePreference.SHORTEST, RoutePreference.BALANCED, RoutePreference.SAFETY)
    }


class AccidentZones:
    def __init__(self, polygons: list):
        self.polygons = polygons
        self._tree = shapely.STRtree(polygons) if polygons else None

    @classmethod
    def from_file(cls, path: Path) -> AccidentZones | None:
        if not path.exists():
            return None
        feats = json.loads(path.read_text(encoding="utf-8"))["features"]
        return cls(
            [
                shape(f["geometry"])
                for f in feats
                if f["properties"]["kind"] not in NON_PEDESTRIAN_KINDS
            ]
        )

    def touches(self, line: LineString) -> bool:
        return self._tree is not None and len(self._tree.query(line, predicate="intersects")) > 0


@dataclass(frozen=True)
class _Piece:
    """잘라낸 망의 무방향 구간 하나(스냅으로 쪼갠 조각 포함)."""

    pid: str
    u: str
    v: str
    coords: tuple[tuple[float, float], ...]


def _length(coords) -> float:
    return sum(haversine_m(*a, *b) for a, b in pairwise(coords))


def _split(seg: Segment, fraction: float) -> tuple[tuple, tuple, tuple[float, float]]:
    line = LineString(seg.coords)
    point = line.interpolate(fraction, normalized=True)
    a = substring(line, 0, fraction, normalized=True)
    b = substring(line, fraction, 1, normalized=True)
    return tuple(a.coords), tuple(b.coords), (point.x, point.y)


class WalkRouter:
    def __init__(self, network: WalkNetwork, zones: AccidentZones | None):
        self.net = network
        self.zones = zones
        self._lock = threading.Lock()  # 기준 거리 계산 때 전체 그래프에 임시 노드를 붙였다 뗀다

    def _attach(self, snap: Snap, name: str) -> tuple[str, list[_Piece], tuple[float, float]]:
        """스냅 지점을 노드로 만든다. 반환: (노드 ID, 원래 구간 대신 쓸 조각들, 스냅 좌표)."""
        seg = snap.segment
        if snap.fraction * seg.length_m <= END_MERGE_M:
            return seg.u, [], seg.coords[0]
        if (1 - snap.fraction) * seg.length_m <= END_MERGE_M:
            return seg.v, [], seg.coords[-1]
        a, b, at = _split(seg, snap.fraction)
        return (
            name,
            [
                _Piece(f"{seg.seg_id}~{name}a", seg.u, name, a),
                _Piece(f"{seg.seg_id}~{name}b", name, seg.v, b),
            ],
            at,
        )

    def plan(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mobility: MobilityProfile = MobilityProfile.STANDARD,
    ) -> RoutePlan:
        """origin/destination: (lon, lat)."""
        so, sd = self.net.snap(*origin), self.net.snap(*destination)
        if so is None or sd is None:
            return RoutePlan(_unavailable(MSG_NOT_NEAR_ROAD))
        snaps = (so.distance_m, sd.distance_m)
        o_node, o_pieces, o_at = self._attach(so, ORIGIN)
        d_node, d_pieces, d_at = self._attach(sd, DESTINATION)
        if so.segment.seg_id == sd.segment.seg_id and o_pieces and d_pieces:
            # 같은 구간에 둘 다 붙으면 그 구간을 세 조각으로 나눈다
            seg = so.segment
            f1, f2 = sorted((so.fraction, sd.fraction))
            n1, n2 = (ORIGIN, DESTINATION) if so.fraction <= sd.fraction else (DESTINATION, ORIGIN)
            line = LineString(seg.coords)
            parts = [
                substring(line, x, y, normalized=True) for x, y in ((0, f1), (f1, f2), (f2, 1))
            ]
            ends = [(seg.u, n1), (n1, n2), (n2, seg.v)]
            o_pieces = [
                _Piece(f"{seg.seg_id}~{i}", u, v, tuple(p.coords))
                for i, ((u, v), p) in enumerate(zip(ends, parts, strict=True))
            ]
            d_pieces = []
        if o_node == d_node:
            return RoutePlan(_unavailable(MSG_TOO_CLOSE), *snaps)
        pieces = o_pieces + d_pieces
        replaced = {
            so.segment.seg_id if o_pieces else None,
            sd.segment.seg_id if d_pieces else None,
        } - {None}

        baseline = self._baseline(o_node, d_node, pieces)
        if baseline is None:
            return RoutePlan(_unavailable(MSG_NOT_CONNECTED), *snaps)
        limit = baseline * (1 + DETOUR_RATIO_POLICY_MAX)
        crop = self._crop(o_at, d_at, limit, replaced) + pieces

        graph = PedestrianGraph(self._edges(crop))
        req = RouteRequest(
            origin=o_node,
            destination=d_node,
            route_preference=RoutePreference.SHORTEST,
            mobility_profile=mobility,
            detour_ratio=DETOUR_RATIO_POLICY_MAX,
        )
        routes = {}
        for pref, found in compare_routes(graph, req).items():
            issues = validate_route(graph, req.model_copy(update={"route_preference": pref}), found)
            res = found
            if issues:
                log.warning("route validation failed: %s", issues)  # 개발자 기록용(ID 포함)
                res = RouteResult(status=RouteStatus.DATA_UNAVAILABLE, caveats=(MSG_CHECK_FAILED,))
            routes[pref] = PlannedRoute(res, self._line(graph, res))
        return RoutePlan(routes, *snaps)

    def _baseline(self, o: str, d: str, pieces: list[_Piece]) -> float | None:
        g = self.net.graph
        with self._lock:
            added = []
            for p in pieces:
                if not g.has_edge(p.u, p.v):
                    g.add_edge(p.u, p.v, length=_length(p.coords))
                    added.append((p.u, p.v))
            try:
                return nx.shortest_path_length(g, o, d, weight="length")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return None
            finally:
                g.remove_edges_from(added)
                g.remove_nodes_from(
                    [n for n in (ORIGIN, DESTINATION) if n in g and g.degree(n) == 0]
                )

    def _crop(self, o_at, d_at, limit: float, replaced: set[str]) -> list[_Piece]:
        """두 초점(출발·도착)까지 거리 합이 limit 이하인 타원 안에 완전히 들어오는 구간만."""
        ox, oy = _TO_M.transform(*o_at)
        dx, dy = _TO_M.transform(*d_at)
        half = limit * 1.01 / 2 + 1.0  # 타원 긴반지름: 바운딩 상자는 중심 ± 긴반지름이면 충분
        cx, cy = (ox + dx) / 2, (oy + dy) / 2
        idx = self.net._tree.query(box(cx - half, cy - half, cx + half, cy + half))
        out = []
        for i in idx:
            s = self.net.segments[int(i)]
            if s.seg_id in replaced:
                continue
            # 기준 거리는 구면 거리, 여기는 UTM-K 평면 거리라
            # 축척 차이(제주에서 0.1% 안팎)만큼 넉넉히 1% 더 잡는다.
            # 더 넓게 자르는 건 계산량만 늘 뿐 결과는 그대로다.
            if all(
                t <= limit * 1.01 + 1.0
                for t in _dist_sum(self.net._lines_m[int(i)], ox, oy, dx, dy)
            ):
                out.append(_Piece(s.seg_id, s.u, s.v, s.coords))
        return out

    def _edges(self, pieces: list[_Piece]) -> list[RoadEdge]:
        edges = []
        sources = (SOURCE_ID, ZONES_SOURCE_ID) if self.zones else (SOURCE_ID,)
        for p in pieces:
            length = _length(p.coords)
            if length <= 0:
                continue
            exposure = (
                (1.0 if self.zones.touches(LineString(p.coords)) else 0.0) if self.zones else None
            )
            for suffix, u, v, coords in (
                ("f", p.u, p.v, p.coords),
                ("r", p.v, p.u, p.coords[::-1]),
            ):
                edges.append(
                    RoadEdge(
                        edge_id=f"{p.pid}:{suffix}",
                        source_node=u,
                        target_node=v,
                        geometry=list(coords),
                        length_m=length,
                        traffic_exposure=exposure,
                        source_ids=sources,
                    )
                )
        return edges

    @staticmethod
    def _line(graph: PedestrianGraph, res: RouteResult) -> list[tuple[float, float]]:
        line: list[tuple[float, float]] = []
        for eid in res.route_edge_ids:
            pts = [(lat, lon) for lon, lat in graph.edge(eid).geometry.coords]
            line.extend(pts if not line else pts[1:])
        return line


def _dist_sum(line_m: LineString, ox: float, oy: float, dx: float, dy: float):
    return [
        ((x - ox) ** 2 + (y - oy) ** 2) ** 0.5 + ((x - dx) ** 2 + (y - dy) ** 2) ** 0.5
        for x, y in line_m.coords
    ]
