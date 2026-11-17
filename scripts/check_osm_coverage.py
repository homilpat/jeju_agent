# ruff: noqa: E501, PLR0912, PLR0915, PLR2004  -- 일회성 점검 스크립트(출력 문구가 긺)
"""OSM 제주 도로망 점검 (docs/DATA_AVAILABILITY.md §5 재현용).

사용: uv run python scripts/check_osm_coverage.py [--download]
데이터는 data/private/osm/ 에 저장(커밋 안 함, ODbL).
"""

import collections
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import networkx as nx

DATA = Path("data/private/osm/jeju_highways.json")
JEJU_RELATION = 2398560  # 제주특별자치도 행정경계(admin_level=4)
QUERY = f'[out:json][timeout:500];area({3600000000 + JEJU_RELATION})->.j;way["highway"](area.j);out body geom;'

NOT_WALK = {
    "motorway",
    "motorway_link",
    "construction",
    "proposed",
    "raceway",
    "platform",
    "corridor",
    "bus_stop",
}
BIG = {
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
}
CAR = BIG | {"motorway", "motorway_link", "unclassified", "residential", "living_street", "road"}
SIDEWALK_KEYS = ("sidewalk", "sidewalk:both", "sidewalk:left", "sidewalk:right")
# 시험 경로 장소(대략 좌표). 스냅 거리가 크면 결과에 표시한다.
PLACES = {
    "공항": (33.5070, 126.4929),
    "동문시장": (33.5116, 126.5262),
    "올레시장": (33.2496, 126.5636),
    "성산일출봉": (33.4581, 126.9425),
    "협재해변": (33.3940, 126.2396),
    "한라수목원": (33.4700, 126.4930),
}
PAIRS = [
    ("공항", "동문시장"),
    ("동문시장", "한라수목원"),
    ("공항", "협재해변"),
    ("공항", "올레시장"),
    ("동문시장", "성산일출봉"),
]


def hv(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    return (
        2
        * 6371000
        * math.asin(
            math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
        )
    )


def download():
    DATA.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": QUERY}).encode(),
        headers={"User-Agent": "jeju-lifepath-coverage-check/0.1"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        DATA.write_bytes(r.read())


def has_sidewalk(t):
    """보도가 이 차도 선에 붙어 있다고 표시된 경우만 참. separate(따로 그린 보도)·no는 거짓."""
    yes = {"both", "left", "right", "yes"}
    return t.get("sidewalk") in yes or any(t.get(k) == "yes" for k in SIDEWALK_KEYS[1:])


def main():
    if "--download" in sys.argv or not DATA.exists():
        download()
    ways = [
        w
        for w in json.loads(DATA.read_text(encoding="utf-8"))["elements"]
        if len(w.get("nodes", [])) >= 2
    ]
    coord = {}
    length_by_type, tagged = collections.Counter(), collections.Counter()
    tag_base = ref_raw = ref_half = big_len = motorroad_len = 0.0
    walk, nobig = nx.Graph(), nx.Graph()
    for w in ways:
        t, h = w["tags"], w["tags"].get("highway")
        pts = [(p["lat"], p["lon"]) for p in w["geometry"]]
        coord.update(zip(w["nodes"], pts, strict=True))
        segs = [
            (w["nodes"][i], w["nodes"][i + 1], hv(pts[i], pts[i + 1])) for i in range(len(pts) - 1)
        ]
        length = sum(s[2] for s in segs)
        length_by_type[h] += length
        if h in CAR or h in {"footway", "path", "pedestrian", "steps"}:
            tag_base += length
            for k in ("lit", "incline", "maxspeed", "surface"):
                tagged[k] += length * (k in t)
            tagged["sidewalk(정보 있음)"] += length * any(k in t for k in SIDEWALK_KEYS)
            tagged["sidewalk(붙은 보도 있음)"] += length * has_sidewalk(t)
        if h in CAR and t.get("ref", "").replace(";", "").isdigit():
            ref_raw += length
            ref_half += length / 2 if t.get("oneway") in ("yes", "-1") else length
        if h in BIG:
            big_len += length
        if t.get("motorroad") == "yes":
            motorroad_len += length
        walkable = (
            h not in NOT_WALK
            and t.get("foot") != "no"
            and t.get("access") not in ("private", "no")
            and t.get("motorroad") != "yes"
        )
        if walkable:
            walk.add_weighted_edges_from(segs)
            if h not in BIG or has_sidewalk(t):
                nobig.add_weighted_edges_from(segs)

    km = lambda m: round(m / 1000, 1)  # noqa: E731
    print("길 개수", len(ways))
    print("종류별 km", {k: km(v) for k, v in length_by_type.most_common(12)})
    print(
        "태그 채움률(길이 기준, 차도+보행로)",
        {k: f"{100 * v / tag_base:.1f}%" for k, v in tagged.items()},
    )
    print(
        f"번호 붙은 차도 km: 일방 그대로 {km(ref_raw)}, 일방 절반 {km(ref_half)} (공식 지방도 개통 711.4)"
    )
    print("큰 차도 km", km(big_len), "자동차전용도로 표시 km", km(motorroad_len))

    comps = sorted(nx.connected_components(walk), key=len, reverse=True)
    island = comps[0]
    weight = lambda g, ns: sum(d["weight"] for _, _, d in g.subgraph(ns).edges(data=True))  # noqa: E731
    island_len = weight(walk, island)
    print("본섬 도보 km", km(island_len))
    print("떨어진 덩어리(1 km 이상): 위치, km")
    for c in comps[1:]:
        if (ln := weight(walk, c)) >= 1000:
            lat = sum(coord[n][0] for n in c) / len(c)
            lon = sum(coord[n][1] for n in c) / len(c)
            print(f"  {lat:.3f},{lon:.3f} {km(ln)}")

    nb = nobig.subgraph(island & set(nobig.nodes))
    best = max(nx.connected_components(nb), key=lambda c: weight(nb, c))
    print(
        f"큰 차도 제외 시 본섬 도보 길이 중 최대 덩어리에 남는 비율 {100 * weight(nb, best) / island_len:.1f}%"
    )

    snap = {k: min(island, key=lambda n, p=p: hv(coord[n], p)) for k, p in PLACES.items()}
    for a, b in PAIRS:
        d_walk = nx.shortest_path_length(walk, snap[a], snap[b], weight="weight")
        straight = hv(coord[snap[a]], coord[snap[b]])  # 스냅된 점 사이 직선(분자와 같은 끝점)
        try:
            d_nb = f"{km(nx.shortest_path_length(nobig, snap[a], snap[b], weight='weight'))} km"
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            d_nb = "끊김"
        print(
            f"  {a}->{b}: 직선 {km(straight)} km, 도보 {km(d_walk)} km (우회율 {d_walk / straight:.2f}), "
            f"큰 차도 제외 {d_nb}, 스냅 {hv(PLACES[a], coord[snap[a]]):.0f}/{hv(PLACES[b], coord[snap[b]]):.0f} m"
        )


if __name__ == "__main__":
    main()
