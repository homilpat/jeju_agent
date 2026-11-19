"""무장애 여행 지식그래프: 공공 무장애 조사 자료(허브·포털)를 장소·대상·지점·주변시설 관계로 묶는다.

LLM은 조건(이름 낱말, 추천 대상, 화장실·주차·주의 지점, 근처)을 구성해 query()를 부르고,
그래프는 실제 자료에 있는 관계만 돌려준다(없는 사실을 만들지 않음). 모두 공공자료라 LLM에 보내도 된다.
"""

from __future__ import annotations

import networkx as nx

from jeju_lifepath.geo.geodesy import haversine_m

POINT_KINDS = ("화장실", "주차장", "쉼터", "경사로", "계단", "오르막", "내리막", "승강기")


def build(places: list[dict]) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for p in places:
        doc = p.get("doc") or {}
        pid = f"place:{p['name']}"
        g.add_node(pid, type="무장애여행지", name=p["name"], address=p.get("address") or "",
                   lat=p["lat"], lon=p["lon"], barrier_points=p.get("barrier_points") or 0,
                   toilet_points=p.get("toilet_points") or 0, has_course=bool(p.get("track")),
                   disabled_toilet=doc.get("disabled_toilet"), disabled_parking=doc.get("disabled_parking"),
                   fee=doc.get("fee"), closed=doc.get("closed"), course=doc.get("course") or "")
        companion = set(doc.get("companion", []))
        for who in doc.get("audience", []):
            g.add_edge(pid, f"audience:{who}", key="RECOMMENDED_FOR", companion=who in companion)
            g.nodes[f"audience:{who}"].update(type="대상", name=who)
        for pt in p.get("points", []):
            kind = next((k for k in POINT_KINDS if k in pt["label"]), None)
            if kind:
                g.add_edge(pid, f"pointkind:{kind}", key="HAS_POINT", info=pt["info"])
                g.nodes[f"pointkind:{kind}"].update(type="지점종류", name=kind)
        for nb in p.get("nearby", []):
            if nb.get("name"):
                nid = f"nearby:{nb['name']}"
                g.add_node(nid, type="주변시설", name=nb["name"], kind=nb.get("kind") or "",
                           wheelchair=nb.get("wheelchair") or "", toilet=nb.get("toilet") or "")
                g.add_edge(pid, nid, key="NEAR")
    return g


def query(  # noqa: PLR0913 - LLM이 구성하는 조건 칸들
    g: nx.MultiDiGraph,
    *,
    name_contains: list[str] | None = None,
    recommended_for: list[str] | None = None,
    without_companion: bool = False,
    has_point: list[str] | None = None,
    disabled_toilet: bool | None = None,
    disabled_parking: bool | None = None,
    max_barrier_points: int | None = None,
    has_course: bool | None = None,
    near: tuple[float, float] | None = None,
    within_km: float | None = None,
    limit: int = 8,
) -> list[dict]:
    """조건에 맞는 무장애 여행지. 조건은 모두 그래프에 있는 속성·관계로만 판정한다.
    자료에 값이 없는 조건(예: 화장실 정보 없음)은 맞는 것으로 치지 않는다."""
    out = []
    for pid, a in g.nodes(data=True):
        if a.get("type") != "무장애여행지":
            continue
        if name_contains and not any(w in a["name"] for w in name_contains):
            continue
        rel = {(v, k): d for _, v, k, d in g.out_edges(pid, keys=True, data=True)}
        if recommended_for:
            edges = [rel.get((f"audience:{w}", "RECOMMENDED_FOR")) for w in recommended_for]
            if any(e is None or (without_companion and e["companion"]) for e in edges):
                continue
        if has_point and any((f"pointkind:{k}", "HAS_POINT") not in rel for k in has_point):
            continue
        toilet = a["disabled_toilet"] == "있음" or a["toilet_points"] > 0
        known = toilet or a["disabled_toilet"] == "없음"
        if disabled_toilet is not None and (not known or toilet != disabled_toilet):
            continue  # 자료에 없으면 조건을 확인할 수 없어 뺀다
        if disabled_parking is not None and a["disabled_parking"] != ("있음" if disabled_parking else "없음"):
            continue
        if max_barrier_points is not None and a["barrier_points"] > max_barrier_points:
            continue
        if has_course is not None and a["has_course"] != has_course:
            continue
        dist = haversine_m(near[0], near[1], a["lon"], a["lat"]) if near else None
        if within_km is not None and dist is not None and dist > within_km * 1000:
            continue
        nearby = [g.nodes[v]["name"] for (v, k) in rel if k == "NEAR"][:3]
        out.append({
            "name": a["name"], "address": a["address"], "lat": a["lat"], "lon": a["lon"],
            "distance_m": round(dist) if dist is not None else None,
            "disabled_toilet": "있음" if toilet else a["disabled_toilet"] or "모름",
            "disabled_parking": a["disabled_parking"] or "모름",
            "barrier_points": a["barrier_points"], "has_course": a["has_course"],
            "recommended_for": [g.nodes[v]["name"] + (" (보호자와 함께)" if d["companion"] else "")
                                for (v, k), d in rel.items() if k == "RECOMMENDED_FOR"],
            "nearby": nearby,
        })
    out.sort(key=lambda x: (x["distance_m"] is None, x["distance_m"] or 0, x["barrier_points"]))
    return out[:limit]
