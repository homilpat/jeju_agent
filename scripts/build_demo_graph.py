"""OSM 원본(data/osm/wondosim.json) → poc/data/graph.js (원도심 DEMO 보행 그래프).

길 모양·거리 = OSM 실제값. 안전환경·와이파이·이동약자·폐쇄 지표 = DEMO 가상값.
비용·통행 제약은 우리 엔진(routing.cost / routing.constraints)으로 계산해 굽는다.
브라우저는 구운 비용으로 탐색만 한다(점수·제약 로직은 여기 한 곳).
공식 데이터가 오면 demo_indicators() 대신 공식 지표를 넣으면 된다.

    python scripts/fetch_osm.py && python scripts/build_demo_graph.py
"""

import json
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "backend"))

from jeju_lifepath.geo.geodesy import haversine_m  # noqa: E402
from jeju_lifepath.routing.constraints import ConstraintEngine, ConstraintProfile  # noqa: E402
from jeju_lifepath.routing.cost import (  # noqa: E402
    IndicatorDirection,
    WeightBasis,
    WeightSet,
    compute_edge_cost,
)
from jeju_lifepath.schemas.common import Passability  # noqa: E402
from jeju_lifepath.schemas.edge import RoadEdge  # noqa: E402

SRC = ROOT / "data" / "osm" / "wondosim.json"
OUT = ROOT / "poc" / "data" / "graph.js"

WALKABLE = {
    "primary", "secondary", "tertiary", "unclassified", "residential", "service",
    "living_street", "pedestrian", "footway", "path", "steps", "track", "corridor",
    "primary_link", "secondary_link", "tertiary_link", "cycleway",
}
MAIN = {"primary", "secondary", "tertiary", "primary_link", "secondary_link", "tertiary_link"}

FAV = IndicatorDirection.FAVOURABLE
PROV = ("DEMO POC 가상 가중치 — G4 근거 아님",)
W = {"lighting_index": 1.0, "cctv_index": 1.0, "activity_index": 0.5}
DIRS = {k: FAV for k in W}
WEIGHTS = {  # 경로 3종 = 같은 엔진, 가중치만 다름
    "short": WeightSet(version="demo-short", basis=WeightBasis.PROVISIONAL_MANUAL,
                       penalty_lambda=0.0, weights={}, directions={}, provenance=PROV),
    "bal": WeightSet(version="demo-bal", basis=WeightBasis.PROVISIONAL_MANUAL,
                     penalty_lambda=1.0, weights=W, directions=DIRS, provenance=PROV),
    "safe": WeightSet(version="demo-safe", basis=WeightBasis.PROVISIONAL_MANUAL,
                      penalty_lambda=4.0, weights=W, directions=DIRS, provenance=PROV),
}


def demo_indicators(way_id: int, hw: str) -> dict:
    """DEMO 가상 지표. way 단위 시드라 양방향·재실행 모두 같은 값."""
    r = random.Random(way_id)
    lo, hi = (0.6, 0.95) if hw in MAIN or hw == "pedestrian" else (0.15, 0.75)
    ind = {k: round(r.uniform(lo, hi), 2) for k in ("lighting_index", "cctv_index", "activity_index")}
    ind["wifi_proximity_index"] = round(r.uniform(lo - 0.1, hi), 2)
    for k in ("cctv_index", "lighting_index"):  # 일부 누락 → 근거 화면의 누락률
        if r.random() < 0.08:
            ind[k] = None
    if hw == "steps":
        acc = Passability.BLOCK
    elif hw in MAIN or hw == "pedestrian":
        acc = Passability.PASS
    else:
        acc = r.choices([Passability.PASS, Passability.BLOCK, Passability.UNKNOWN], [60, 15, 25])[0]
    closure = r.choices(["OPEN", "CLOSED", "UNKNOWN"], [97, 1, 2])[0]
    return {**ind, "accessibility": acc, "closure_status": closure}


def main() -> None:
    els = json.loads(SRC.read_text(encoding="utf-8"))["elements"]
    ways = [e for e in els if e["type"] == "way" and e.get("tags", {}).get("highway") in WALKABLE
            and e["tags"].get("foot") != "no" and e["tags"].get("access") != "private"]

    # 교차점(2개 이상 way가 공유) + 끝점에서 way를 자른다.
    use: dict[int, int] = {}
    for w in ways:
        for n in w["nodes"]:
            use[n] = use.get(n, 0) + 1
    node_idx: dict[int, int] = {}
    nodes: list[list[float]] = []

    def idx(osm_id: int, pt: dict) -> int:
        if osm_id not in node_idx:
            node_idx[osm_id] = len(nodes)
            nodes.append([round(pt["lon"], 6), round(pt["lat"], 6)])
        return node_idx[osm_id]

    engine = ConstraintEngine()
    std, wc = ConstraintProfile(), ConstraintProfile(requires_step_free=True, verified_only=True)
    geoms: list[list[list[float]]] = []
    edges: list[list] = []
    for w in ways:
        hw, ids, pts = w["tags"]["highway"], w["nodes"], w["geometry"]
        ind = demo_indicators(w["id"], hw)
        start = 0
        for i in range(1, len(ids)):
            if i != len(ids) - 1 and use[ids[i]] < 2:
                continue
            seg = [[round(p["lon"], 6), round(p["lat"], 6)] for p in pts[start:i + 1]]
            length = sum(haversine_m(*seg[k], *seg[k + 1]) for k in range(len(seg) - 1))
            if length < 0.5:
                start = i
                continue
            a, b = idx(ids[start], pts[start]), idx(ids[i], pts[i])
            g = len(geoms)
            geoms.append(seg)
            # 보행자는 일방통행 무관 → 양방향 edge 2개 (RoadEdge 규약)
            for d, (u, v, line) in enumerate(((a, b, seg), (b, a, seg[::-1]))):
                e = RoadEdge(edge_id=f"w{w['id']}_{start}_{d}", source_node=str(u), target_node=str(v),
                             geometry=line, length_m=length, source_ids=("DEMO_osm_wondosim",), **ind)
                c = {k: compute_edge_cost(e, ws) for k, ws in WEIGHTS.items()}
                v_std = engine.evaluate_edge(e, std).verdict
                v_wc = engine.evaluate_edge(e, wc).verdict
                # flags: 1=일반 탐색 가능, 2=휠체어(PASS만), 4=일반인데 미확인(UNKNOWN)
                flags = (v_std is not Passability.BLOCK) | (v_wc is Passability.PASS) << 1 \
                    | (v_std is Passability.UNKNOWN) << 2
                # [from, to, 길이, 균형비용, 안전비용, flags, geom, 역방향, 조명, CCTV, 와이파이]
                edges.append([u, v, round(length, 1), round(c["bal"].cost, 1), round(c["safe"].cost, 1),
                              flags, g, d, ind["lighting_index"], ind["cctv_index"],
                              ind["wifi_proximity_index"]])
            start = i

    facilities = [{"type": e["tags"]["amenity"], "name": e["tags"].get("name", ""),
                   "lon": e["lon"], "lat": e["lat"]}
                  for e in els if e["type"] == "node" and e.get("tags", {}).get("amenity") in ("toilets", "pharmacy")]
    named = {}
    for e in els:
        name = e.get("tags", {}).get("name")
        if not name or "highway" in e["tags"]:
            continue
        c = e.get("center") or (e if "lat" in e else None)
        if c is None and e.get("geometry"):
            g = e["geometry"]
            c = {"lat": sum(p["lat"] for p in g) / len(g), "lon": sum(p["lon"] for p in g) / len(g)}
        if c:
            named[name.replace(" ", "")] = [round(c["lon"], 6), round(c["lat"], 6)]

    data = {"built": date.today().isoformat(), "source": "© OpenStreetMap contributors (ODbL) + DEMO 가상 지표",
            "nodes": nodes, "geoms": geoms, "edges": edges, "facilities": facilities, "named": named,
            "weights": {k: ws.version for k, ws in WEIGHTS.items()}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.GRAPH=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";",
                   encoding="utf-8")
    print(f"nodes {len(nodes)} edges {len(edges)} facilities {len(facilities)} -> {OUT}")


if __name__ == "__main__":
    main()
