from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from shapely import LineString

from jeju_lifepath.schemas import (
    DETOUR_RATIO_POLICY_MAX,
    INDICATOR_FIELDS,
    ClosureStatus,
    GeoPoint,
    MobilityProfile,
    Passability,
    RoadEdge,
    RoutePreference,
    RouteRequest,
    RouteResult,
    RouteStatus,
)
from tests.backend.fixtures.synthetic.grid import SYNTHETIC_SOURCE_ID, make_edge

KST = timezone(timedelta(hours=9))


def _edge_kwargs(**over):
    base = {
        "edge_id": "e1",
        "source_node": "n1",
        "target_node": "n2",
        "geometry": [(126.53, 33.50), (126.531, 33.50)],
        "length_m": 92.7,
        "source_ids": [SYNTHETIC_SOURCE_ID],
    }
    base.update(over)
    return base


# ----------------------------------------------------------------- RoadEdge
class TestRoadEdge:
    def test_defaults_are_unknown_not_pass(self):
        edge = RoadEdge(**_edge_kwargs())
        assert edge.accessibility is Passability.UNKNOWN
        assert edge.closure_status is ClosureStatus.UNKNOWN
        assert all(v is None for v in edge.indicator_values().values())
        assert isinstance(edge.geometry, LineString)

    @pytest.mark.parametrize("value", [0.0, 1.0, 0.5])
    def test_indicator_boundaries_accepted(self, value):
        edge = RoadEdge(**_edge_kwargs(cctv_index=value))
        assert edge.cctv_index == value
        assert edge.available_indicators() == ("cctv_index",)

    @pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf")])
    def test_indicator_out_of_range_rejected(self, value):
        with pytest.raises(ValidationError):
            RoadEdge(**_edge_kwargs(lighting_index=value))

    @pytest.mark.parametrize("length", [0.0, -1.0, float("nan")])
    def test_non_positive_length_rejected(self, length):
        with pytest.raises(ValidationError):
            RoadEdge(**_edge_kwargs(length_m=length))

    @pytest.mark.parametrize(
        "geometry",
        [
            [(126.53, 33.50)],  # single vertex
            [(126.53, 33.50), (126.53, 33.50)],  # degenerate
            [(33.50, 226.53), (33.50, 226.54)],  # lat/lon swapped & out of range
            [(126.53, 33.50, 1.0), (126.54, 33.50, 1.0)],  # 3D
            "LINESTRING (0 0, 1 1)",  # WKT strings not accepted implicitly
        ],
    )
    def test_invalid_geometry_rejected(self, geometry):
        with pytest.raises(ValidationError):
            RoadEdge(**_edge_kwargs(geometry=geometry))

    def test_requires_provenance(self):
        with pytest.raises(ValidationError):
            RoadEdge(**_edge_kwargs(source_ids=[]))

    def test_source_ids_deduplicated_in_order(self):
        edge = RoadEdge(**_edge_kwargs(source_ids=["b", "a", "b"]))
        assert edge.source_ids == ("b", "a")

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValidationError):
            RoadEdge(**_edge_kwargs(observed_at=datetime(2026, 11, 1, 12)))

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            RoadEdge(**_edge_kwargs(crime_probability=0.3))

    def test_immutable(self):
        edge = RoadEdge(**_edge_kwargs())
        with pytest.raises(ValidationError):
            edge.length_m = 5.0

    def test_json_round_trip(self):
        edge = make_edge(
            "A-B", "A", "B", cctv_index=0.4, observed_at=datetime(2026, 11, 1, tzinfo=KST)
        )
        restored = RoadEdge.model_validate_json(edge.model_dump_json())
        assert restored == edge
        assert edge.model_dump(mode="json")["geometry"][0] == [126.53, 33.5]

    def test_indicator_registry_matches_schema(self):
        assert set(INDICATOR_FIELDS) <= set(RoadEdge.model_fields)


# ------------------------------------------------------------- RouteRequest
def _req(**over):
    base = {
        "origin": (33.50, 126.53),
        "destination": "동문시장",
        "route_preference": RoutePreference.SAFETY,
        "detour_ratio": 0.2,
    }
    base.update(over)
    return RouteRequest(**base)


class TestRouteRequest:
    def test_tuple_is_lat_lon(self):
        req = _req()
        assert req.origin == GeoPoint(lat=33.50, lon=126.53)
        assert req.destination == "동문시장"
        assert req.mobility_profile is MobilityProfile.STANDARD

    def test_swapped_tuple_rejected(self):
        with pytest.raises(ValidationError):
            _req(origin=(126.53, 33.50))

    @pytest.mark.parametrize("ratio", [0.0, DETOUR_RATIO_POLICY_MAX])
    def test_detour_boundaries(self, ratio):
        assert _req(detour_ratio=ratio).detour_ratio == ratio

    @pytest.mark.parametrize("ratio", [-0.01, DETOUR_RATIO_POLICY_MAX + 0.01, float("nan")])
    def test_detour_out_of_policy(self, ratio):
        with pytest.raises(ValidationError):
            _req(detour_ratio=ratio)

    def test_route_preference_required(self):
        # Dialogue layer must ask; the routing contract never defaults it.
        with pytest.raises(ValidationError):
            RouteRequest(origin="a", destination="b", detour_ratio=0.1)

    def test_same_origin_destination_rejected(self):
        with pytest.raises(ValidationError):
            _req(origin="제주시청", destination="제주시청")

    def test_poi_categories_normalised(self):
        req = _req(poi_categories=["Pharmacy", "toilet", "pharmacy"])
        assert req.poi_categories == ("pharmacy", "toilet")

    def test_departure_time_must_be_tz_aware(self):
        assert _req(departure_time=datetime(2026, 11, 3, 21, tzinfo=KST)).departure_time
        with pytest.raises(ValidationError):
            _req(departure_time=datetime(2026, 11, 3, 21))

    def test_empty_place_rejected(self):
        with pytest.raises(ValidationError):
            _req(destination="   ")


# -------------------------------------------------------------- RouteResult
class TestRouteResult:
    def test_ok_result(self):
        res = RouteResult(
            status=RouteStatus.OK,
            route_edge_ids=["A-B", "B-C"],
            length_m=185.4,
            estimated_minutes=2.6,
            coverage={"cctv_index": 0.5},
            provenance=[SYNTHETIC_SOURCE_ID],
        )
        assert res.route_edge_ids == ("A-B", "B-C")

    def test_unverified_requires_caveat(self):
        with pytest.raises(ValidationError):
            RouteResult(status=RouteStatus.UNVERIFIED, route_edge_ids=["A-B"], length_m=90.0)
        res = RouteResult(
            status=RouteStatus.UNVERIFIED,
            route_edge_ids=["A-B"],
            length_m=90.0,
            caveats=["accessibility UNKNOWN on A-B"],
        )
        assert res.status is RouteStatus.UNVERIFIED

    @pytest.mark.parametrize("status", [RouteStatus.OK, RouteStatus.UNVERIFIED])
    def test_routed_status_requires_edges(self, status):
        with pytest.raises(ValidationError):
            RouteResult(status=status, length_m=10.0, caveats=["x"])

    @pytest.mark.parametrize(
        "status", [RouteStatus.NO_FEASIBLE_ROUTE, RouteStatus.DATA_UNAVAILABLE]
    )
    def test_failure_status_cannot_carry_route(self, status):
        with pytest.raises(ValidationError):
            RouteResult(status=status, route_edge_ids=["A-B"], length_m=1.0, caveats=["x"])
        with pytest.raises(ValidationError):
            RouteResult(status=status)  # no caveat
        ok = RouteResult(status=status, caveats=["no verified step-free route"])
        assert ok.length_m is None

    def test_repeated_edges_allowed_at_schema_level(self):
        # Out-and-back via a POI may reuse an edge; graph-level validation
        # (validation.route) is responsible for continuity checks.
        res = RouteResult(status=RouteStatus.OK, route_edge_ids=["A-B", "B-A"], length_m=2.0)
        assert res.route_edge_ids == ("A-B", "B-A")

    def test_coverage_range(self):
        with pytest.raises(ValidationError):
            RouteResult(
                status=RouteStatus.OK,
                route_edge_ids=["A-B"],
                length_m=1.0,
                coverage={"cctv_index": 1.2},
            )


def test_utc_aware_datetime_accepted():
    edge = RoadEdge(**_edge_kwargs(observed_at=datetime(2026, 1, 1, tzinfo=UTC)))
    assert edge.observed_at.tzinfo is not None
