"""좌표 두 개 -> 실제 제주 보행망에서 세 경로(최단·균형·안전환경) 계산.

1) 두 좌표를 가장 가까운 보행 구간에 붙이고(스냅), 그 구간을 잘라 출발·도착 노드를 만든다.
2) 전체망에서 최단 거리 L0를 구한다. 우회 상한 r 안의 경로는 모두 "출발·도착을 초점으로 하고
   두 초점까지 거리 합이 L0(1+r) 이하인 타원" 안에 있으므로, 그 안의 구간만 잘라 계산한다
   (정확한 잘라내기).
3) 잘라낸 구간을 RoadEdge로 만들고 사고다발 구역 지표를 붙여 compare_routes() -> validate_route().
"""

from __future__ import annotations

import csv
import heapq
import io
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import networkx as nx
import numpy as np
import shapely
from shapely.geometry import LineString, box, shape

from jeju_lifepath.geo.geodesy import haversine_m
from jeju_lifepath.geo.graph import PedestrianGraph
from jeju_lifepath.geo.osm import _TO_M, SOURCE_ID, Snap, WalkNetwork, add_base_edge
from jeju_lifepath.routing.constraints import DEFAULT_MOBILITY_POLICIES
from jeju_lifepath.routing.optimizer import compare_routes
from jeju_lifepath.schemas import (
    MobilityProfile,
    Passability,
    RoadEdge,
    RouteRequest,
    RouteResult,
    RouteStatus,
)
from jeju_lifepath.schemas.common import RoutePreference
from jeju_lifepath.schemas.request import DETOUR_RATIO_POLICY_MAX
from jeju_lifepath.validation.route import validate_route

log = logging.getLogger(__name__)

ORIGIN, DESTINATION = "출발", "도착"
ZONES_SOURCE_ID = "accident_zones"
CCTV_SOURCE_ID = "public_cctv"
# 사용자가 "CCTV 근처 길"을 고르면,
# 길에서 이 거리 안에 공개 방범 CCTV가 있으면 그 길을 CCTV 있는 길로 본다.
# 근거: 강용길·염윤호(2020) CCTV 범죄예방 효과 분석의 영향 반경 100m. 사용자 승인(2026-11-17)
CCTV_RADIUS_M = 100.0
# 보행자 안전과 관련된 설치 목적만 쓴다
# (교통단속·시설물관리·재난재해 등은 보행자 쪽을 비추지 않을 수 있음)
CCTV_PURPOSES = frozenset({"생활방범", "어린이보호"})
# 보행 경로라 자전거 사고 다발구역은 빼고,
# 나머지(전체·보행노인·보행어린이·스쿨존어린이)는 모두 보행자에게 해당
NON_PEDESTRIAN_KINDS = frozenset({"자전거"})
# 스냅 지점이 구간 끝에서 이 거리 안이면 새 노드를 만들지 않고 끝 노드를 그대로 쓴다
# (길이 0 구간 방지용, 결과에 영향 거의 없음)
END_MERGE_M = 1.0
# 출발·도착 점이 가장 가까운 길에서 이보다 멀면 경로를 만들지 않는다
# (엉뚱한 길에 붙은 경로를 확인된 경로처럼 보여주지 않기, REQUIREMENTS 12.8).
# shortcut: 근거 없는 잠정값 300m, 시연 지점 실측 후 조정
MAX_SNAP_M = 300.0

MSG_NOT_NEAR_ROAD = "출발지나 도착지 근처에서 걸을 수 있는 길을 찾지 못했습니다."
MSG_TOO_FAR_FROM_ROAD = "출발지나 도착지가 걸을 수 있는 길에서 너무 멀어 경로를 안내하지 않습니다."
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


class CctvPoints:
    """공개 CCTV 위치(UTM-K 미터 좌표). 길에서 CCTV_RADIUS_M 안에 있는지만 본다."""

    def __init__(self, lonlats: list[tuple[float, float]]):
        self.points_m = [shapely.Point(_TO_M.transform(lon, lat)) for lon, lat in lonlats]
        self._tree = shapely.STRtree(self.points_m) if self.points_m else None

    @classmethod
    def from_csvs(cls, paths: list[Path]) -> CctvPoints | None:
        """두 형식(cp949): 허브 510(설치목적·위도·경도),
        표준데이터(설치목적구분·WGS84위도·WGS84경도)."""
        seen: set[tuple[float, float]] = set()
        for path in paths:
            if not path.exists():
                continue
            for row in csv.DictReader(io.StringIO(path.read_bytes().decode("cp949"))):
                purpose = row.get("설치목적구분") or row.get("설치목적")
                lat = row.get("WGS84위도") or row.get("위도")
                lon = row.get("WGS84경도") or row.get("경도")
                if purpose in CCTV_PURPOSES and lat and lon:
                    # 두 파일에 같은 곳이 있을 수 있어 좌표(약 1m 단위)로 겹침 제거
                    seen.add((round(float(lon), 5), round(float(lat), 5)))
        return cls(sorted(seen)) if seen else None

    def near(self, line: LineString) -> bool:
        if self._tree is None:
            return False
        line_m = shapely.transform(
            line, lambda xy: np.column_stack(_TO_M.transform(xy[:, 0], xy[:, 1]))
        )
        return len(self._tree.query(line_m, predicate="dwithin", distance=CCTV_RADIUS_M)) > 0


@dataclass(frozen=True)
class _Piece:
    """잘라낸 망의 무방향 구간 하나(스냅으로 쪼갠 조각 포함)."""

    pid: str
    u: str
    v: str
    coords: tuple[tuple[float, float], ...]
    steps: bool = False


def _length(coords) -> float:
    return sum(haversine_m(*a, *b) for a, b in pairwise(coords))


class WalkRouter:
    def __init__(
        self,
        network: WalkNetwork,
        zones: AccidentZones | None,
        cctv: CctvPoints | None = None,
    ):
        self.net = network
        self.zones = zones
        self.cctv = cctv
        self._lock = threading.Lock()  # 기준 거리 계산 때 전체 그래프에 임시 노드를 붙였다 뗀다

    def _attach(self, snap: Snap, name: str) -> tuple[str, list[_Piece], tuple[float, float]]:
        """스냅 지점을 노드로 만든다. 반환: (노드 ID, 원래 구간 대신 쓸 조각들, 스냅 좌표)."""
        seg = snap.segment
        if snap.fraction * seg.length_m <= END_MERGE_M:
            return seg.u, [], seg.coords[0]
        if (1 - snap.fraction) * seg.length_m <= END_MERGE_M:
            return seg.v, [], seg.coords[-1]
        a, b = self.net.cut(seg, [snap.fraction])
        return (
            name,
            [
                _Piece(f"{seg.seg_id}~{name}a", seg.u, name, a, seg.steps),
                _Piece(f"{seg.seg_id}~{name}b", name, seg.v, b, seg.steps),
            ],
            a[-1],
        )

    def plan(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mobility: MobilityProfile = MobilityProfile.STANDARD,
        prefer_cctv: bool = False,
    ) -> RoutePlan:
        """origin/destination: (lon, lat).
        prefer_cctv: 사용자가 CCTV 근처 길 안내를 골랐을 때만 CCTV 지표를 넣는다."""
        so, sd = self.net.snap(*origin), self.net.snap(*destination)
        if so is None or sd is None:
            return RoutePlan(_unavailable(MSG_NOT_NEAR_ROAD))
        snaps = (so.distance_m, sd.distance_m)
        if max(snaps) > MAX_SNAP_M:
            return RoutePlan(_unavailable(MSG_TOO_FAR_FROM_ROAD), *snaps)
        same = so.segment.seg_id == sd.segment.seg_id
        if same and abs(so.fraction - sd.fraction) * so.segment.length_m <= END_MERGE_M:
            return RoutePlan(_unavailable(MSG_TOO_CLOSE), *snaps)
        o_node, o_pieces, o_at = self._attach(so, ORIGIN)
        d_node, d_pieces, d_at = self._attach(sd, DESTINATION)
        if same and o_pieces and d_pieces:
            # 같은 구간에 둘 다 붙으면 그 구간을 세 조각으로 나눈다
            seg = so.segment
            f1, f2 = sorted((so.fraction, sd.fraction))
            n1, n2 = (ORIGIN, DESTINATION) if so.fraction <= sd.fraction else (DESTINATION, ORIGIN)
            parts = self.net.cut(seg, [f1, f2])
            ends = [(seg.u, n1), (n1, n2), (n2, seg.v)]
            o_pieces = [
                _Piece(f"{seg.seg_id}~{i}", u, v, p, seg.steps)
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

        step_free = DEFAULT_MOBILITY_POLICIES[mobility].requires_step_free
        baseline = self._baseline(o_node, d_node, pieces, step_free)
        if baseline is None:
            return RoutePlan(_unavailable(MSG_NOT_CONNECTED), *snaps)
        limit = baseline * (1 + DETOUR_RATIO_POLICY_MAX)
        crop = self._crop(o_at, d_at, limit, replaced) + pieces

        graph = PedestrianGraph(self._edges(crop, prefer_cctv and self.cctv is not None))
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

    def distances_from(
        self,
        source: tuple[float, float],
        targets: list[tuple[float, float]],
        mobility: MobilityProfile = MobilityProfile.STANDARD,
        cutoff_m: float | None = None,
    ) -> list[float | None]:
        """source(lon, lat)에서 targets 각각까지 보행망 최단 거리(m). 한 번의 탐색으로 모두 구한다
        (§11.4 우회 시간 정렬용). 닿지 못하거나 cutoff를 넘으면 None.
        스냅 거리(점에서 길까지)는 경로 길이와 같은 기준으로 넣지 않는다."""
        policy = DEFAULT_MOBILITY_POLICIES[mobility]
        src = self.net.snap(*source)
        # 기준 보행망에는 통행 확인(PASS) 정보가 없어(계단만 앎) 확인 구간만 쓰는 휠체어는 갈 곳이 없다(§4.3)
        if src is None or src.distance_m > MAX_SNAP_M or policy.verified_only:
            return [None] * len(targets)
        key = "flat" if policy.requires_step_free else "length"
        seg = src.segment
        g = self.net.graph
        with self._lock:
            g.add_edge("_src", seg.u, length=src.fraction * seg.length_m)
            g.add_edge("_src", seg.v, length=(1 - src.fraction) * seg.length_m)
            for e in (g["_src"][seg.u], g["_src"][seg.v]):
                e["flat"] = None if seg.steps else e["length"]
            try:
                dist = nx.single_source_dijkstra_path_length(
                    g, "_src", cutoff=cutoff_m, weight=lambda _u, _v, e: e[key]
                )
            finally:
                g.remove_node("_src")
        out: list[float | None] = []
        for lon, lat in targets:
            t = self.net.snap(lon, lat)
            if t is None or t.distance_m > MAX_SNAP_M or (t.segment.steps and key == "flat"):
                out.append(None)
                continue
            s, f = t.segment, t.fraction
            if s.seg_id == seg.seg_id:
                out.append(abs(f - src.fraction) * s.length_m)
                continue
            options = [
                dist[n] + part
                for n, part in ((s.u, f * s.length_m), (s.v, (1 - f) * s.length_m))
                if n in dist
            ]
            best = min(options) if options else None
            out.append(None if best is None or (cutoff_m and best > cutoff_m) else best)
        return out

    def nearest_source_distances(
        self,
        sources: list[tuple[float, float]],
        area,
        mobility: MobilityProfile = MobilityProfile.STANDARD,
        until: float | None = None,
    ) -> list[tuple[tuple[float, float], float | None]] | None:
        """area(경도·위도 shapely 도형) 안의 길 위 지점(교차점·길 끝·꺾이는 점, 보행망 노드)마다
        가장 가까운 source(시설)까지 보행망 최단 거리(m). 시설 전부를 출발점으로 한 탐색 한 번
        (§2 E01 공백 분석). 닿는 시설이 없으면 None. 스냅 거리(시설에서 길까지)는 distances_from과
        같이 넣지 않는다. 공유 그래프를 바꾸지 않아 잠금이 필요 없다.
        until(time.monotonic 기준)을 넘기면 TimeoutError. 확인 구간만 쓰는 이동(휠체어)은 None."""
        policy = DEFAULT_MOBILITY_POLICIES[mobility]
        if policy.verified_only:
            return None
        key = "flat" if policy.requires_step_free else "length"
        g = self.net.graph
        heap: list[tuple[float, str]] = []
        for lon, lat in sources:
            s = self.net.snap(lon, lat)
            if s is None or s.distance_m > MAX_SNAP_M or (s.segment.steps and key == "flat"):
                continue
            seg = s.segment
            heap += [(s.fraction * seg.length_m, seg.u), ((1 - s.fraction) * seg.length_m, seg.v)]
        heapq.heapify(heap)
        dist: dict[str, float] = {}
        while heap:
            if until is not None and len(dist) % 1000 == 0 and time.monotonic() > until:
                raise TimeoutError("area access search")
            d, u = heapq.heappop(heap)
            if u in dist:
                continue
            dist[u] = d
            for v, e in g[u].items():
                w = e[key]
                if w is not None and v not in dist:
                    heapq.heappush(heap, (d + w, v))
        nodes = list(self.net.node_lonlat.items())
        xy = np.array([at for _, at in nodes])
        inside = shapely.contains_xy(area, xy[:, 0], xy[:, 1])
        return [(at, dist.get(n)) for (n, at), ok in zip(nodes, inside, strict=True) if ok]

    def _baseline(self, o: str, d: str, pieces: list[_Piece], step_free: bool) -> float | None:
        """전체망 최단 거리. 계단을 못 가는 사람은 계단 구간을 뺀 거리
        (타원이 그 사람의 경로를 덮도록)."""
        g = self.net.graph
        key = "flat" if step_free else "length"
        with self._lock:
            added = []
            for p in pieces:
                if add_base_edge(g, p.u, p.v, _length(p.coords), p.steps):
                    added.append((p.u, p.v))
            try:
                return nx.shortest_path_length(g, o, d, weight=lambda _u, _v, e: e[key])
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
                out.append(_Piece(s.seg_id, s.u, s.v, s.coords, s.steps))
        return out

    def _edges(self, pieces: list[_Piece], use_cctv: bool = False) -> list[RoadEdge]:
        edges = []
        sources = (SOURCE_ID,) + ((ZONES_SOURCE_ID,) if self.zones else ())
        sources += (CCTV_SOURCE_ID,) if use_cctv else ()
        for p in pieces:
            length = _length(p.coords)
            if length <= 0:
                continue
            exposure = (
                (1.0 if self.zones.touches(LineString(p.coords)) else 0.0) if self.zones else None
            )
            # CCTV 자료는 제주 전역 공개 목록이라 0은 "반경 안에 공개 방범 CCTV 없음"이라는 관측값
            cctv = (1.0 if self.cctv.near(LineString(p.coords)) else 0.0) if use_cctv else None
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
                        cctv_index=cctv,
                        # 계단은 확인된 장벽, 나머지는 확인 안 됨(일반 보행자에겐 둘 다 통과)
                        accessibility=Passability.BLOCK if p.steps else Passability.UNKNOWN,
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
