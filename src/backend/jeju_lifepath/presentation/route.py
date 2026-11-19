"""RouteResult -> user-facing Korean text. Screens show only this module's output
(no ids, enum codes or JSON; REQUIREMENTS §7)."""

from __future__ import annotations

from collections.abc import Mapping

from jeju_lifepath.routing.optimizer import CLOSURE_UNCHECKED
from jeju_lifepath.schemas import RoutePreference, RouteResult, RouteStatus

# Shown once on the start screen; never repeated per route.
START_NOTICE = (
    "안전환경 우선 경로는 교통사고가 자주 난 구역을 덜 지나도록 고른 길입니다. "
    "실제 안전을 보장하지는 않으니 참고용으로 이용해 주세요. 공사·통제 정보는 반영하지 못했습니다."
)

STATUS_TEXT = {
    RouteStatus.OK: "경로를 찾았습니다.",
    RouteStatus.UNVERIFIED: "경로를 찾았습니다.",  # 확인 못 한 점은 아래 줄(caveats)과 시작 화면 안내가 말함
    RouteStatus.NO_FEASIBLE_ROUTE: "조건에 맞는 경로를 찾지 못했습니다.",
    RouteStatus.DATA_UNAVAILABLE: "필요한 정보가 없어 경로를 안내할 수 없습니다.",
}

PREF_TEXT = {
    RoutePreference.SHORTEST: "최단 경로",
    RoutePreference.BALANCED: "균형 경로",
    RoutePreference.SAFETY: "안전환경 우선 경로",
}

INDICATOR_TEXT = {
    "cctv_index": "CCTV 인접도",
    "lighting_index": "조명 인접도",
    "crime_context": "지역 범죄 통계 수준",
    "traffic_exposure": "사고 다발 구역을 지나는 비율",
    "weather_exposure": "기상 영향",
}


def _topic(word: str) -> str:
    """받침이 있으면 '은', 없으면 '는'."""
    last = word[-1]
    has_final = "가" <= last <= "힣" and (ord(last) - ord("가")) % 28 != 0
    return "은" if has_final else "는"


def _source_name(source_id: str, names: Mapping[str, str]) -> str:
    if source_id in names:
        return names[source_id]
    if source_id.startswith("synthetic:"):
        return "시연용 합성 데이터(실제 제주 데이터 아님)"
    return "출처 확인 필요 자료"


def _distance(m: float) -> str:
    return f"{m / 1000:.1f}km" if m >= 1000 else f"{round(m)}m"  # noqa: PLR2004


def render_route(res: RouteResult, source_names: Mapping[str, str] | None = None) -> str:
    """``source_names`` maps source ids to display names (e.g. from the data registry)."""
    lines = [STATUS_TEXT[res.status]]
    if res.length_m is not None:
        lines.append(f"거리 약 {_distance(res.length_m)}, 이동 약 {res.estimated_minutes:g}분")
    cov = res.coverage.get("indicator_coverage")
    if cov is not None:
        lines.append(f"환경 지표 자료가 있는 정도: 약 {round(cov * 100)}%")
    if res.provenance:
        names = dict.fromkeys(_source_name(s, source_names or {}) for s in res.provenance)
        lines.append("자료 출처: " + ", ".join(names))
    # 통제 미확인은 모든 길에 같은 말이라 시작 화면(START_NOTICE)에서 한 번만 말한다
    lines += [f"· {c}" for c in res.caveats if c != CLOSURE_UNCHECKED]
    return "\n".join(lines)


def _relative_lines(results: Mapping[RoutePreference, RouteResult]) -> list[str]:
    # Relative wording only: fixed high/low cut-offs would need evidence we do not have.
    out = []
    for name, label in INDICATOR_TEXT.items():
        vals = {
            p: v for p, r in results.items() if (v := r.indicator_breakdown.get(name)) is not None
        }
        if len(set(vals.values())) < 2:  # noqa: PLR2004 - need two distinct values to compare
            continue
        top = max(vals, key=vals.__getitem__)
        out.append(f"{label}{_topic(label)} {PREF_TEXT[top]}가 가장 높습니다.")
    return out


def render_comparison(
    results: Mapping[RoutePreference, RouteResult], source_names: Mapping[str, str] | None = None
) -> str:
    blocks = [
        f"■ {PREF_TEXT.get(pref, '경로')}\n{render_route(r, source_names)}"
        for pref, r in results.items()
    ]
    rel = _relative_lines(results)
    if rel:
        blocks.append("■ 경로 비교\n" + "\n".join(rel))
    return "\n\n".join(blocks)


SOURCE_NAMES = {
    "osm:jeju_highways": "오픈스트리트맵 도로",
    "accident_zones": "교통사고 다발구역(도로교통공단·공공데이터포털)",
    "public_cctv": "공개 방범·어린이보호 CCTV(제주데이터허브·공공데이터포털)",
}


def _snap_line(label: str, metres: float | None) -> list[str]:
    return (
        []
        if metres is None
        else [f"{label} 위치에서 가장 가까운 길까지 약 {round(metres)}m 떨어져 있어요."]
    )


def render_plan(plan) -> dict:
    """routing.walk.RoutePlan -> 화면용 값. 문장은 모두 한국어, 경로선은 지도용 (위도, 경도) 목록.
    'kind'는 화면 코드가 경로 종류 버튼과 맞추는 데만 쓰고 화면에 표시하지 않는다."""
    results = {p: r.result for p, r in plan.routes.items()}
    return {
        "routes": [
            {
                "kind": pref.value,
                "title": PREF_TEXT[pref],
                "found": r.result.length_m is not None,
                "minutes": r.result.estimated_minutes,
                "text": render_route(r.result, SOURCE_NAMES).split("\n"),
                "line": [list(p) for p in r.line],
            }
            for pref, r in plan.routes.items()
        ],
        "comparison": _relative_lines(results),
        "notes": _snap_line("출발", plan.origin_snap_m)
        + _snap_line("도착", plan.destination_snap_m),
    }
