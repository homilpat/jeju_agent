"""CA-MOR 비용: cost = length_m * (1 + lambda * q), q는 가용 지표만 재정규화한 불리함 점수.

가중치는 잠정값(근거 검증 전, G4에서 교체). q는 실제 범죄확률이 아니다.
"""

from jeju_lifepath.schemas import RoadEdge, RoutePreference

WEIGHTS_VERSION = "provisional-v0"

# 지표명 -> (가중치, 값이 높을수록 불리한가)
# activity_index는 사용자 목적(고립회피/혼잡회피)에 따라 방향이 달라 기본 제외.
WEIGHTS: dict[str, tuple[float, bool]] = {
    "cctv_index": (1.0, False),
    "lighting_index": (1.0, False),
    "crime_context": (1.0, True),
    "traffic_exposure": (1.0, True),
    "weather_exposure": (0.5, True),
}

LAMBDA = {
    RoutePreference.SHORTEST: 0.0,
    RoutePreference.BALANCED: 1.0,
    RoutePreference.SAFETY: 3.0,
}

# 지표가 하나도 없을 때의 q. 0이면 데이터 없는 구간이 유리해 보이므로 중립값을 쓴다.
NO_DATA_Q = 0.5


def adverse_score(e: RoadEdge) -> tuple[float, float]:
    """(q, coverage). coverage = 가용 가중치 / 전체 가중치."""
    total = sum(w for w, _ in WEIGHTS.values())
    num = den = 0.0
    for name, (w, higher_is_worse) in WEIGHTS.items():
        val = getattr(e, name)
        if val is None:
            continue
        num += w * (val if higher_is_worse else 1 - val)
        den += w
    return (num / den if den else NO_DATA_Q), den / total


def edge_cost(e: RoadEdge, pref: RoutePreference) -> float:
    q, _ = adverse_score(e)
    return e.length_m * (1 + LAMBDA[pref] * q)
