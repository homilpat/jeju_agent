import pytest

from jeju_lifepath.routing.constraints import (
    AccessibilityRule,
    ClosureRule,
    ConstraintEngine,
    ConstraintProfile,
    ConstraintReason,
    MobilityPolicy,
)
from jeju_lifepath.schemas import (
    ClosureStatus,
    MobilityProfile,
    Passability,
    RoutePreference,
    RouteRequest,
)
from tests.backend.fixtures.synthetic.grid import build_graph

STANDARD = ConstraintProfile()
STEP_FREE = ConstraintProfile(requires_step_free=True)
VERIFIED = ConstraintProfile(requires_step_free=True, verified_only=True)

CLOSED = {"closure_status": ClosureStatus.CLOSED}
CLOSURE_UNKNOWN = {"closure_status": ClosureStatus.UNKNOWN}
STAIRS = {"accessibility": Passability.BLOCK}
ACCESS_UNKNOWN = {"accessibility": Passability.UNKNOWN}


def _evaluate(overrides, profile):
    g = build_graph(overrides)
    return g, ConstraintEngine().evaluate(g.edges(), profile)


def _request(**over):
    base = {
        "origin": "A",
        "destination": "C",
        "route_preference": RoutePreference.SHORTEST,
        "detour_ratio": 0.2,
    }
    return RouteRequest(**{**base, **over})


# ------------------------------------------------------------- profiles
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, STANDARD),
        ({"avoid_stairs": True}, STEP_FREE),
        ({"mobility_profile": MobilityProfile.STROLLER}, STEP_FREE),
        ({"mobility_profile": MobilityProfile.WHEELCHAIR}, STEP_FREE),  # 확인 안 된 길은 경고와 함께(2026-11-19)
        ({"require_verified_accessibility": True}, VERIFIED),
    ],
)
def test_profile_from_request(kwargs, expected):
    assert ConstraintProfile.from_request(_request(**kwargs)) == expected


def test_custom_policy_overrides_default():
    policies = {MobilityProfile.STANDARD: MobilityPolicy(True, True)}
    assert ConstraintProfile.from_request(_request(), policies) == VERIFIED
    with pytest.raises(ValueError, match="no mobility policy"):
        ConstraintProfile.from_request(
            _request(mobility_profile=MobilityProfile.WHEELCHAIR), policies
        )


# ---------------------------------------------------------- edge verdicts
def test_all_open_and_accessible_pass():
    g, ev = _evaluate(None, VERIFIED)
    assert ev.verified_edge_ids == g.edge_ids
    assert ev.blocked_edge_ids == ev.unknown_edge_ids == frozenset()


@pytest.mark.parametrize("profile", [STANDARD, STEP_FREE, VERIFIED])
def test_closed_blocks_for_everyone(profile):
    _, ev = _evaluate({"BC": CLOSED}, profile)
    assert ev.blocked_edge_ids == {"B-C", "C-B"}
    assert ev.blocked_edge_ids.isdisjoint(ev.searchable_edge_ids)
    assert ev.blocked_edge_ids.isdisjoint(ev.relaxed_edge_ids)


def test_closure_unknown_is_never_pass():
    _, ev = _evaluate({"BC": CLOSURE_UNKNOWN}, STANDARD)
    assert ev.verdict("B-C") is Passability.UNKNOWN
    assert "B-C" in ev.searchable_edge_ids  # standard users may traverse, flagged
    _, ev = _evaluate({"BC": CLOSURE_UNKNOWN}, VERIFIED)
    assert "B-C" not in ev.searchable_edge_ids


def test_stairs_do_not_block_standard_pedestrian():
    _, ev = _evaluate({"BC": STAIRS}, STANDARD)
    assert ev.verdict("B-C") is Passability.PASS
    assert all(r.rule != "accessibility" for r in ev.results["B-C"].reasons)


@pytest.mark.parametrize("profile", [STEP_FREE, VERIFIED])
def test_stairs_block_step_free_profiles(profile):
    _, ev = _evaluate({"BC": STAIRS}, profile)
    assert ev.verdict("B-C") is Passability.BLOCK


def test_access_unknown_handling():
    _, step_free = _evaluate({"BC": ACCESS_UNKNOWN}, STEP_FREE)
    assert step_free.verdict("B-C") is Passability.UNKNOWN
    assert "B-C" in step_free.searchable_edge_ids
    _, verified = _evaluate({"BC": ACCESS_UNKNOWN}, VERIFIED)
    assert "B-C" not in verified.searchable_edge_ids
    assert "B-C" in verified.unknown_edge_ids


def test_worst_of_combination_and_reasons():
    _, ev = _evaluate({"BC": {**CLOSED, **ACCESS_UNKNOWN}}, STEP_FREE)
    res = ev.results["B-C"]
    assert res.verdict is Passability.BLOCK
    assert {r.code for r in res.reasons} == {"CLOSED", "ACCESS_UNKNOWN"}


def test_counts_cover_every_edge():
    g, ev = _evaluate({"BC": CLOSED, "EF": ACCESS_UNKNOWN}, STEP_FREE)
    counts = ev.counts()
    assert sum(counts.values()) == len(g)
    assert counts[Passability.BLOCK] == 2
    assert counts[Passability.UNKNOWN] == 2


# ------------------------------------------------------------ extensibility
class NightOnlyRule:
    """Example extra rule: proves new constraints plug in without engine edits."""

    name = "test_extra"

    def evaluate(self, edge, profile):
        if edge.edge_id == "A-B":
            return ConstraintReason(self.name, "TEST", Passability.UNKNOWN, "extra rule")
        return None


def test_custom_rule_plugs_in():
    engine = ConstraintEngine([ClosureRule(), AccessibilityRule(), NightOnlyRule()])
    ev = engine.evaluate(build_graph().edges(), STANDARD)
    assert ev.verdict("A-B") is Passability.UNKNOWN


def test_duplicate_rule_names_rejected():
    with pytest.raises(ValueError, match="duplicate rule"):
        ConstraintEngine([ClosureRule(), ClosureRule()])


def test_duplicate_edges_rejected():
    edges = list(build_graph().edges())
    with pytest.raises(ValueError, match="duplicate edge_id"):
        ConstraintEngine().evaluate([*edges, edges[0]], STANDARD)


# ------------------------------------------- integration with graph search
def test_block_never_appears_in_baseline_route():
    g, ev = _evaluate({"BC": CLOSED}, STANDARD)
    path = g.shortest_path_by_length("A", "C", ev.searchable_edge_ids)
    assert path is not None
    assert set(path.edge_ids).isdisjoint(ev.blocked_edge_ids)
    assert g.validate_path(path.edge_ids) == []
    assert path.edge_ids == ("A-B", "B-E", "E-F", "F-C")


def test_verified_only_avoids_unknown_then_reports_absence():
    overrides = {"BC": CLOSED, "BE": ACCESS_UNKNOWN}
    g, ev = _evaluate(overrides, VERIFIED)
    path = g.shortest_path_by_length("A", "C", ev.searchable_edge_ids)
    assert path.edge_ids == ("A-D", "D-E", "E-F", "F-C")
    assert set(path.edge_ids) <= ev.verified_edge_ids

    # Also make A-D unknown: no verified route exists, relaxed one does (UNVERIFIED).
    g, ev = _evaluate({**overrides, "AD": ACCESS_UNKNOWN}, VERIFIED)
    assert g.shortest_path_by_length("A", "C", ev.searchable_edge_ids) is None
    relaxed = g.shortest_path_by_length("A", "C", ev.relaxed_edge_ids)
    assert relaxed is not None
    assert set(relaxed.edge_ids) & ev.unknown_edge_ids


def test_wheelchair_blocked_everywhere_has_no_route():
    g, ev = _evaluate({"BC": CLOSED, "EF": STAIRS}, VERIFIED)
    assert g.shortest_path_by_length("A", "C", ev.searchable_edge_ids) is None
    assert g.shortest_path_by_length("A", "C", ev.relaxed_edge_ids) is None
