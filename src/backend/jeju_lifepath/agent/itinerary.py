"""일정 계산과 검증(§11.2 탐욕적 선택을 그대로 쓴다. 최적 조합이라고 주장하지 않는다).

stop(일정의 장소 한 곳): {"ref", "query", "theme", "fixed", "completed"} + 계산 중에만 쓰는
"name", "category", "lon", "lat", "url"(카카오 자료, 저장하지 않음).
"""

from __future__ import annotations

from collections.abc import Callable

DistFn = Callable[[tuple[float, float], list[tuple[float, float]]], list[float | None]]


def _pt(p: dict) -> tuple[float, float]:
    return (p["lon"], p["lat"])


def plan_greedy(
    start: dict,
    end: dict,
    pool: list[dict],
    dist: DistFn,
    m_per_min: float,
    *,
    round_trip: bool,
    stop_count: int | None,
    budget_min: float | None,
    stay_min: int,
    theme_quota: dict[str, int],
    max_stops: int,
) -> tuple[list[dict], dict]:
    """다음 후보 규칙 (a) 도착지에 가까워짐(왕복이면 생략) (b) 이미 고른 곳 제외 (c) 우회 시간 오름차순,
    동률은 이름순. 1순위를 넣으면 시간을 넘거나 개수를 채우면 멈춘다(그다음 순위로 건너뛰지 않음).
    반환: (고른 장소 목록, 관찰 기록 {unreachable, stopped_by})."""
    to_end = dict(
        zip((p["ref"] for p in pool), dist(_pt(end), [_pt(p) for p in pool]), strict=True)
    )
    unreachable = {r for r, d in to_end.items() if d is None}
    chosen: list[dict] = []
    used_quota: dict[str, int] = {}
    cur = start
    elapsed = 0.0  # 분: 지금까지 걸은 시간 + 머문 시간
    stopped_by = "no_candidates"
    limit = min(stop_count or max_stops, max_stops)
    while len(chosen) < limit:
        left = [
            p
            for p in pool
            if p["ref"] not in unreachable
            and p["ref"] not in {c["ref"] for c in chosen}
            and used_quota.get(p["theme"], 0) < theme_quota.get(p["theme"], limit)
        ]
        if not left:
            stopped_by = "no_candidates"
            break
        d_cur = dist(_pt(cur), [_pt(p) for p in left] + [_pt(end)])
        cur_to_end = d_cur[-1]
        ranked = []
        for p, d in zip(left, d_cur[:-1], strict=True):
            if d is None:
                unreachable.add(p["ref"])
                continue
            if not round_trip and cur_to_end is not None and to_end[p["ref"]] >= cur_to_end:
                continue  # (a) 되돌아가지 않음
            detour = d + to_end[p["ref"]] - (cur_to_end or 0)
            ranked.append((detour, p["name"], d, p))
        if not ranked:
            stopped_by = "no_candidates"
            break
        ranked.sort(key=lambda x: (x[0], x[1]))
        _, _, d, best = ranked[0]
        finish = elapsed + d / m_per_min + stay_min + to_end[best["ref"]] / m_per_min
        if budget_min is not None and finish > budget_min:
            stopped_by = "time"
            break
        chosen.append(best)
        used_quota[best["theme"]] = used_quota.get(best["theme"], 0) + 1
        elapsed += d / m_per_min + stay_min
        cur = best
    else:
        stopped_by = "count"
    return chosen, {"unreachable": len(unreachable), "stopped_by": stopped_by}


def legs(start: dict, end: dict, stops: list[dict], dist: DistFn, m_per_min: float) -> list[dict]:
    """남은 일정의 구간별 걷는 거리·시간. 다녀온 곳은 건너뛰고 마지막으로 다녀온 곳에서 출발한다."""
    done = [s for s in stops if s.get("completed")]
    cur = done[-1] if done and "lon" in done[-1] else start
    out = []
    for s in [s for s in stops if not s.get("completed")] + [end]:
        if "lon" not in s:
            out.append({"to": s.get("ref"), "metres": None, "minutes": None})
            continue
        if "lon" not in cur:
            out.append({"to": s.get("ref"), "metres": None, "minutes": None})
        else:
            m = dist(_pt(cur), [_pt(s)])[0]
            out.append(
                {
                    "to": s.get("ref"),
                    "metres": m,
                    "minutes": None if m is None else round(m / m_per_min),
                }
            )
        cur = s
    return out


def validate(
    stops: list[dict],
    leg_list: list[dict],
    *,
    stay_min: int,
    budget_min: float | None,
    fixed_refs: set[str],
    excluded_refs: set[str],
    completed_refs: set[str],
) -> dict:
    """계산 도구와 따로 보는 검증(AG-304). errors가 있으면 게시하지 않는다."""
    errors, warnings = [], []
    refs = [s["ref"] for s in stops]
    if len(refs) != len(set(refs)):
        errors.append("duplicate_stop")
    if excluded_refs & set(refs):
        errors.append("excluded_included")
    if not fixed_refs <= set(refs):
        errors.append("fixed_missing")
    remaining = [s for s in stops if s["ref"] not in completed_refs]
    if any(s.get("completed") and s["ref"] not in completed_refs for s in stops):
        errors.append("completed_mismatch")
    if any(lg["minutes"] is None for lg in leg_list):
        warnings.append("leg_unknown")
        if budget_min is not None:  # 모르는 구간을 0분으로 셈하면 기한을 지킨다고 말할 수 없다
            errors.append("leg_unknown_with_deadline")
    total = sum(lg["minutes"] or 0 for lg in leg_list) + stay_min * len(remaining)
    if budget_min is not None and total > budget_min:
        errors.append("over_time")
    return {
        "errors": errors,
        "warnings": warnings,
        "total_minutes": total,
        "publishable": not errors,
    }
