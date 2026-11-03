"""CA-MOR edge cost foundation (B02 cost term; REQUIREMENTS §4.5, ARCHITECTURE §5).

    cost(e) = length_m(e) * (1 + lambda * q(e))
    q(e)    = sum_i(w_i * r_i(e)) / sum_i(w_i : indicator i observed on e)

``r_i`` is the *adverse-oriented* value of indicator i: ``value`` for ADVERSE
indicators and ``1 - value`` for FAVOURABLE ones. The direction is declared by
the ``WeightSet`` — not by the edge — so e.g. activity_index can mean
"isolation" or "crowding" depending on user purpose (REQUIREMENTS §5).

No weights ship with the code. Every WeightSet carries version, basis and
provenance; values must come from G4 (evidence + sensitivity). Paper effect
sizes (e.g. 13 %/14 %) must not be used as weights.

``q`` is a weighted combination of observed indicators and is NOT a crime
probability. Missing indicators are excluded by re-normalisation and reported
via ``coverage``/``missing_indicators``; they are never treated as 0 risk. If no
weighted indicator is observed, ``q`` is ``None`` and the cost degenerates to
length with ``coverage == 0`` — callers must surface that as uncertainty.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import Field, model_validator

from jeju_lifepath.schemas.common import FrozenModel, NonEmptyStr
from jeju_lifepath.schemas.edge import INDICATOR_FIELDS, RoadEdge


class IndicatorDirection(StrEnum):
    ADVERSE = "ADVERSE"  # higher value = less desirable environment
    FAVOURABLE = "FAVOURABLE"  # higher value = more desirable environment


class WeightBasis(StrEnum):
    ESTIMATED = "ESTIMATED"  # statistically estimated & validated on Jeju data
    LITERATURE_DIRECTION = "LITERATURE_DIRECTION"  # direction/constraints from research
    PROVISIONAL_MANUAL = "PROVISIONAL_MANUAL"  # explainable provisional + sensitivity
    TEST_ONLY = "TEST_ONLY"  # synthetic fixtures; must never reach users


NonNegative = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]


class WeightSet(FrozenModel):
    version: NonEmptyStr
    basis: WeightBasis
    penalty_lambda: NonNegative
    weights: Mapping[NonEmptyStr, NonNegative]
    directions: Mapping[NonEmptyStr, IndicatorDirection]
    provenance: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
    notes: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> WeightSet:
        unknown = (set(self.weights) | set(self.directions)) - set(INDICATOR_FIELDS)
        if unknown:
            raise ValueError(f"unknown indicators {sorted(unknown)}; allowed {INDICATOR_FIELDS}")
        undirected = {k for k, w in self.weights.items() if w > 0} - set(self.directions)
        if undirected:
            raise ValueError(f"weighted indicators need a declared direction: {sorted(undirected)}")
        if self.penalty_lambda > 0 and not any(w > 0 for w in self.weights.values()):
            raise ValueError("penalty_lambda > 0 requires at least one positive weight")
        return self

    @property
    def active_indicators(self) -> tuple[str, ...]:
        return tuple(k for k in INDICATOR_FIELDS if self.weights.get(k, 0.0) > 0)


@dataclass(frozen=True)
class EdgeCost:
    edge_id: str
    length_m: float
    adverse_score: float | None  # q(e) in [0,1]; None if nothing observed
    cost: float
    coverage: float  # observed share of active weight, [0,1]
    missing_indicators: tuple[str, ...]


def adverse_value(value: float, direction: IndicatorDirection) -> float:
    return value if direction is IndicatorDirection.ADVERSE else 1.0 - value


def compute_edge_cost(edge: RoadEdge, weight_set: WeightSet) -> EdgeCost:
    active = weight_set.active_indicators
    total_w = sum(weight_set.weights[k] for k in active)
    observed_w = 0.0
    weighted = 0.0
    missing = []
    for name in active:
        value = getattr(edge, name)
        if value is None:
            missing.append(name)
            continue
        w = weight_set.weights[name]
        observed_w += w
        weighted += w * adverse_value(value, weight_set.directions[name])
    q = weighted / observed_w if observed_w > 0 else None
    cost = edge.length_m * (1.0 + weight_set.penalty_lambda * (q or 0.0))
    coverage = observed_w / total_w if total_w > 0 else 1.0
    return EdgeCost(edge.edge_id, edge.length_m, q, cost, coverage, tuple(missing))


def compute_edge_costs(
    edges: Mapping[str, RoadEdge] | list[RoadEdge], weight_set: WeightSet
) -> dict[str, EdgeCost]:
    items = edges.values() if isinstance(edges, Mapping) else edges
    return {e.edge_id: compute_edge_cost(e, weight_set) for e in items}
