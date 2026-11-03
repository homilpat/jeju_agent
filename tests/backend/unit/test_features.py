from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from jeju_lifepath.geo.features import (
    EdgeFeatureAnalyzer,
    FeatureConflictError,
    FeatureLayer,
    apply_feature_layers,
)
from jeju_lifepath.geo.graph import PedestrianGraph, UnknownEdgeError
from jeju_lifepath.schemas import ClosureStatus, Passability, Resolution
from tests.backend.fixtures.synthetic_grid import SYNTHETIC_SOURCE_ID, build_graph

KST = timezone(timedelta(hours=9))
T1 = datetime(2026, 10, 1, tzinfo=KST)
T2 = datetime(2026, 11, 1, tzinfo=KST)


def _layer(field="cctv_index", values=None, **over):
    base = {
        "field": field,
        "analyzer_id": f"test:{field}",
        "values": values if values is not None else {"A-B": 0.7},
        "source_ids": [f"synthetic:{field}"],
    }
    base.update(over)
    return FeatureLayer(**base)


class ConstantAnalyzer:
    """Minimal analyser used to verify the protocol (synthetic)."""

    analyzer_id = "test:constant"
    field = "lighting_index"

    def compute(self, graph: PedestrianGraph) -> FeatureLayer:
        return _layer(self.field, dict.fromkeys(sorted(graph.edge_ids), 0.5))


def test_analyzer_protocol():
    analyzer = ConstantAnalyzer()
    assert isinstance(analyzer, EdgeFeatureAnalyzer)
    g, prov = apply_feature_layers(build_graph(), [analyzer.compute(build_graph())])
    assert all(e.lighting_index == 0.5 for e in g.edges())
    assert prov[0].edge_count == len(g)


def test_merge_values_provenance_and_freshness():
    g0 = build_graph(
        {
            "AB": {"closure_status": ClosureStatus.UNKNOWN},
            "BC": {"accessibility": Passability.UNKNOWN},
        }
    )
    layers = [
        _layer(values={"A-B": 0.7}, observed_at=T2, resolution=Resolution(spatial="point")),
        _layer("crime_context", {"A-B": 0.2}, observed_at=T1, caveats=["읍면동 단위 맥락"]),
        _layer("closure_status", {"A-B": "CLOSED"}),
        _layer("accessibility", {"B-C": Passability.BLOCK}),
    ]
    g, prov = apply_feature_layers(g0, layers)
    ab = g.edge("A-B")
    assert (ab.cctv_index, ab.crime_context) == (0.7, 0.2)
    assert ab.closure_status is ClosureStatus.CLOSED
    assert g.edge("B-C").accessibility is Passability.BLOCK
    assert ab.observed_at == T1  # oldest input wins
    assert ab.source_ids[0] == SYNTHETIC_SOURCE_ID
    assert {"synthetic:cctv_index", "synthetic:crime_context"} <= set(ab.source_ids)
    assert "읍면동 단위 맥락" in ab.quality.caveats
    # untouched edges keep missing values
    assert g.edge("D-E").cctv_index is None
    assert [p.field for p in prov] == [layer.field for layer in layers]
    assert prov[0].resolution.spatial == "point"
    # topology and original graph unchanged
    assert g.edge_ids == g0.edge_ids
    assert g0.edge("A-B").cctv_index is None


def test_unknown_edge_rejected():
    with pytest.raises(UnknownEdgeError):
        apply_feature_layers(build_graph(), [_layer(values={"Z-Z": 0.1})])


def test_conflicting_layers_rejected():
    with pytest.raises(FeatureConflictError):
        apply_feature_layers(build_graph(), [_layer(), _layer()])


def test_overwrite_existing_value_rejected():
    g, _ = apply_feature_layers(build_graph(), [_layer()])
    with pytest.raises(FeatureConflictError):
        apply_feature_layers(g, [_layer()])
    # fixture edges are already OPEN: a closure layer must not silently overwrite
    with pytest.raises(FeatureConflictError):
        apply_feature_layers(build_graph(), [_layer("closure_status", {"A-B": "CLOSED"})])


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("cctv_index", {"A-B": 1.5}),
        ("accessibility", {"A-B": "MAYBE"}),
    ],
)
def test_values_revalidated_through_schema(field, values):
    with pytest.raises(ValidationError):
        apply_feature_layers(
            build_graph({"AB": {"accessibility": "UNKNOWN"}}), [_layer(field, values)]
        )


def test_layer_contract_validation():
    with pytest.raises(ValidationError):
        _layer("crime_probability")
    with pytest.raises(ValidationError):
        _layer(source_ids=[])
    with pytest.raises(ValidationError):
        _layer("accessibility", {"A-B": None})
