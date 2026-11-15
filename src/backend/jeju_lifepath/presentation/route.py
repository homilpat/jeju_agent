"""RouteResult -> user-facing Korean text. Screens show only this module's output
(no ids, enum codes or JSON; REQUIREMENTS §7)."""

from __future__ import annotations

from collections.abc import Mapping

from jeju_lifepath.schemas import RoutePreference, RouteResult, RouteStatus

# Shown once on the start screen; never repeated per route.
START_NOTICE = (
    "안전환경 우선 경로는 CCTV·가로등이 설치된 위치를 참고해 고른 길입니다. "
    "실제 안전을 보장하지는 않으니 참고용으로 이용해 주세요."
)

STATUS_TEXT = {
    RouteStatus.OK: "경로를 찾았습니다.",
    RouteStatus.UNVERIFIED: "경로를 찾았지만, 일부 구간은 통행 가능 여부가 확인되지 않았습니다.",
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
    "traffic_exposure": "보행 사고 노출",
    "weather_exposure": "기상 영향",
}


def _source_name(source_id: str) -> str:
    # shortcut: prefix match only, switch to data/registry display names once G1 data lands
    if source_id.startswith("synthetic:"):
        return "시연용 합성 데이터(실제 제주 데이터 아님)"
    return "출처 확인 필요 자료"


def _level(v: float) -> str:
    return "높음" if v >= 0.67 else "보통" if v >= 0.34 else "낮음"  # noqa: PLR2004


def _distance(m: float) -> str:
    return f"{m / 1000:.1f}km" if m >= 1000 else f"{round(m)}m"  # noqa: PLR2004


def render_route(res: RouteResult) -> str:
    lines = [STATUS_TEXT[res.status]]
    if res.length_m is not None:
        lines.append(f"거리 약 {_distance(res.length_m)}, 도보 약 {res.estimated_minutes:g}분")
    levels = [
        f"{label} {_level(v)}"
        for name, label in INDICATOR_TEXT.items()
        if (v := res.indicator_breakdown.get(name)) is not None
    ]
    if levels:
        lines.append("경로 주변 환경: " + ", ".join(levels))
    cov = res.coverage.get("indicator_coverage")
    if cov is not None:
        lines.append(f"환경 지표 자료가 있는 정도: 약 {round(cov * 100)}%")
    if res.provenance:
        names = dict.fromkeys(_source_name(s) for s in res.provenance)
        lines.append("자료 출처: " + ", ".join(names))
    lines += [f"· {c}" for c in res.caveats]
    return "\n".join(lines)


def render_comparison(results: Mapping[RoutePreference, RouteResult]) -> str:
    return "\n\n".join(
        f"■ {PREF_TEXT.get(pref, '경로')}\n{render_route(r)}" for pref, r in results.items()
    )
