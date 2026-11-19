"""Hard-constraint engine (B01; REQUIREMENTS §4.2–4.3, ARCHITECTURE §5).

Per edge, independent rules return PASS/BLOCK/UNKNOWN (or ``None`` when not
applicable to the profile). Verdicts combine as worst-of BLOCK > UNKNOWN > PASS.

Guarantees:
* BLOCK edges are never part of any searchable edge set.
* UNKNOWN is never upgraded to PASS. A closure update that failed is UNKNOWN,
  not OPEN.
* When the profile demands verified accessibility, only PASS edges are
  searchable; UNKNOWN edges are exposed separately so the router (G6) can tell
  the user that no verified route exists and, if it offers a fallback, label
  it UNVERIFIED.

New constraints (weather, time windows, staleness …) are added as additional
``ConstraintRule`` implementations; the engine itself does not change.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from jeju_lifepath.schemas.common import ClosureStatus, MobilityProfile, Passability
from jeju_lifepath.schemas.edge import RoadEdge
from jeju_lifepath.schemas.request import RouteRequest

_SEVERITY = {Passability.PASS: 0, Passability.UNKNOWN: 1, Passability.BLOCK: 2}


@dataclass(frozen=True)
class MobilityPolicy:
    requires_step_free: bool
    verified_only: bool


# Policy for each declared mobility profile. WHEELCHAIR and STROLLER avoid confirmed
# barriers (stairs, OSM wheelchair=no) but may traverse UNKNOWN edges; the result is then
# UNVERIFIED and says how much of it is unverified. WHEELCHAIR was PASS-only (REQUIREMENTS §4.3),
# but only ~1% of Jeju ways are verified, so it almost never routed; user decision 2026-11-19:
# route with a warning. Callers can still demand PASS-only via require_verified_accessibility.
DEFAULT_MOBILITY_POLICIES: Mapping[MobilityProfile, MobilityPolicy] = MappingProxyType(
    {
        MobilityProfile.STANDARD: MobilityPolicy(requires_step_free=False, verified_only=False),
        MobilityProfile.WHEELCHAIR: MobilityPolicy(requires_step_free=True, verified_only=False),
        MobilityProfile.STROLLER: MobilityPolicy(requires_step_free=True, verified_only=False),
    }
)


@dataclass(frozen=True)
class ConstraintProfile:
    """Constraint-relevant view of a request."""

    requires_step_free: bool = False
    verified_only: bool = False

    @classmethod
    def from_request(
        cls,
        request: RouteRequest,
        policies: Mapping[MobilityProfile, MobilityPolicy] = DEFAULT_MOBILITY_POLICIES,
    ) -> ConstraintProfile:
        try:
            policy = policies[request.mobility_profile]
        except KeyError:
            raise ValueError(f"no mobility policy for {request.mobility_profile}") from None
        verified = policy.verified_only or request.require_verified_accessibility
        step_free = policy.requires_step_free or request.avoid_stairs or verified
        return cls(requires_step_free=step_free, verified_only=verified)


@dataclass(frozen=True)
class ConstraintReason:
    rule: str
    code: str
    verdict: Passability
    detail: str


class ConstraintRule(Protocol):
    name: str

    def evaluate(self, edge: RoadEdge, profile: ConstraintProfile) -> ConstraintReason | None:
        """Return a reason with verdict, or None if the rule does not apply."""


class ClosureRule:
    """Official closures apply to every user."""

    name = "closure"

    def evaluate(self, edge: RoadEdge, profile: ConstraintProfile) -> ConstraintReason | None:
        status = edge.closure_status
        if status is ClosureStatus.CLOSED:
            return ConstraintReason(self.name, "CLOSED", Passability.BLOCK, "officially closed")
        if status is ClosureStatus.UNKNOWN:
            return ConstraintReason(
                self.name,
                "CLOSURE_UNKNOWN",
                Passability.UNKNOWN,
                "closure status unavailable; not assumed OPEN",
            )
        return ConstraintReason(self.name, "OPEN", Passability.PASS, "open")


class AccessibilityRule:
    """Step-free accessibility (stairs/slope/kerbs) — only for profiles that need it.

    For a standard pedestrian, a staircase (accessibility BLOCK) is walkable, so
    the rule is not applicable rather than blocking.
    """

    name = "accessibility"

    def evaluate(self, edge: RoadEdge, profile: ConstraintProfile) -> ConstraintReason | None:
        if not profile.requires_step_free:
            return None
        verdict = edge.accessibility
        detail = {
            Passability.PASS: "step-free access confirmed",
            Passability.BLOCK: "confirmed barrier (stairs/steep slope/kerb)",
            Passability.UNKNOWN: "accessibility not verified",
        }[verdict]
        return ConstraintReason(self.name, f"ACCESS_{verdict}", verdict, detail)


def default_rules() -> tuple[ConstraintRule, ...]:
    return (ClosureRule(), AccessibilityRule())


@dataclass(frozen=True)
class EdgeConstraintResult:
    edge_id: str
    verdict: Passability
    reasons: tuple[ConstraintReason, ...]


@dataclass(frozen=True)
class ConstraintEvaluation:
    profile: ConstraintProfile
    results: Mapping[str, EdgeConstraintResult] = field(repr=False)

    def verdict(self, edge_id: str) -> Passability:
        return self.results[edge_id].verdict

    def edge_ids_with(self, verdict: Passability) -> frozenset[str]:
        return frozenset(k for k, r in self.results.items() if r.verdict is verdict)

    @property
    def blocked_edge_ids(self) -> frozenset[str]:
        return self.edge_ids_with(Passability.BLOCK)

    @property
    def unknown_edge_ids(self) -> frozenset[str]:
        return self.edge_ids_with(Passability.UNKNOWN)

    @property
    def verified_edge_ids(self) -> frozenset[str]:
        return self.edge_ids_with(Passability.PASS)

    @property
    def relaxed_edge_ids(self) -> frozenset[str]:
        """PASS ∪ UNKNOWN — never contains BLOCK."""
        return self.verified_edge_ids | self.unknown_edge_ids

    @property
    def searchable_edge_ids(self) -> frozenset[str]:
        """Primary search set for this profile."""
        return self.verified_edge_ids if self.profile.verified_only else self.relaxed_edge_ids

    def counts(self) -> dict[Passability, int]:
        return {v: len(self.edge_ids_with(v)) for v in Passability}


class ConstraintEngine:
    def __init__(self, rules: Sequence[ConstraintRule] | None = None) -> None:
        self.rules: tuple[ConstraintRule, ...] = tuple(rules) if rules else default_rules()
        names = [r.name for r in self.rules]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate rule names: {names}")

    def evaluate_edge(self, edge: RoadEdge, profile: ConstraintProfile) -> EdgeConstraintResult:
        reasons = tuple(
            r for r in (rule.evaluate(edge, profile) for rule in self.rules) if r is not None
        )
        verdict = max(
            (r.verdict for r in reasons), key=_SEVERITY.__getitem__, default=Passability.PASS
        )
        return EdgeConstraintResult(edge.edge_id, verdict, reasons)

    def evaluate(
        self, edges: Iterable[RoadEdge], profile: ConstraintProfile
    ) -> ConstraintEvaluation:
        results: dict[str, EdgeConstraintResult] = {}
        for edge in edges:
            if edge.edge_id in results:
                raise ValueError(f"duplicate edge_id {edge.edge_id!r}")
            results[edge.edge_id] = self.evaluate_edge(edge, profile)
        return ConstraintEvaluation(profile, MappingProxyType(dict(sorted(results.items()))))
