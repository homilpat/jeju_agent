"""Edge-feature producer interface shared by analysers A01–A07.

Each analyser computes *one* RoadEdge field for edges of an existing
``PedestrianGraph`` and returns a ``FeatureLayer``. Layers are merged with
``apply_feature_layers`` which:

* only accepts ``edge_id``s that exist in the graph (no new identities);
* never changes topology;
* re-validates every merged edge through the ``RoadEdge`` schema;
* refuses to silently overwrite a value already present (indicator conflict);
* leaves edges absent from the layer unchanged (missing stays ``None``/UNKNOWN);
* records per-layer provenance in ``FeatureProvenance``, because RoadEdge has a
  single ``resolution``/``observed_at`` whereas indicators come from sources of
  different granularity (see docs/IMPLEMENTATION_STATUS.md, contract gap C-4).

Analysers must not know about routing or cost.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Protocol, runtime_checkable

from pydantic import AwareDatetime, Field, field_validator, model_validator

from jeju_lifepath.geo.graph import PedestrianGraph, UnknownEdgeError
from jeju_lifepath.schemas.common import ClosureStatus, FrozenModel, NonEmptyStr, Passability
from jeju_lifepath.schemas.edge import FEATURE_FIELDS, INDICATOR_FIELDS, Resolution, RoadEdge
from jeju_lifepath.schemas.feature import FeatureValue
from jeju_lifepath.schemas.source import DataQuality

# Units of each RoadEdge feature field. Indicators are dimensionless [0,1]
# indices whose meaning is defined by the producing analyser.
FIELD_UNITS: Mapping[str, str] = {
    **dict.fromkeys(INDICATOR_FIELDS, "index[0,1]"),
    "accessibility": "PASS|BLOCK|UNKNOWN",
    "closure_status": "OPEN|CLOSED|UNKNOWN",
}


class FeatureConflictError(ValueError):
    """Two sources supplied a value for the same edge field."""


class FeatureLayer(FrozenModel):
    """Values of one RoadEdge field produced by one analyser run."""

    field: NonEmptyStr
    analyzer_id: NonEmptyStr
    values: Mapping[NonEmptyStr, Any]
    source_ids: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
    observed_at: AwareDatetime | None = None
    resolution: Resolution = Resolution()
    quality: DataQuality = DataQuality()

    @property
    def caveats(self) -> tuple[str, ...]:
        return self.quality.caveats

    @property
    def unit(self) -> str:
        return FIELD_UNITS[self.field]

    def feature_value(self, edge_id: str) -> FeatureValue:
        """Self-describing record for one edge (missing edges -> value None)."""
        return FeatureValue(
            edge_id=edge_id,
            field=self.field,
            value=self.values.get(edge_id),
            unit=self.unit,
            observed_at=self.observed_at,
            resolution=self.resolution,
            source_ids=self.source_ids,
            quality=self.quality,
        )

    @field_validator("field")
    @classmethod
    def _known_field(cls, value: str) -> str:
        if value not in FEATURE_FIELDS:
            raise ValueError(f"unknown feature field {value!r}; expected one of {FEATURE_FIELDS}")
        return value

    @model_validator(mode="after")
    def _status_values_are_not_none(self) -> FeatureLayer:
        # For status fields, "no information" must be expressed as UNKNOWN.
        if self.field not in INDICATOR_FIELDS and any(v is None for v in self.values.values()):
            raise ValueError(f"{self.field} values must be explicit (use UNKNOWN, not None)")
        return self


class FeatureProvenance(FrozenModel):
    field: NonEmptyStr
    analyzer_id: NonEmptyStr
    source_ids: tuple[NonEmptyStr, ...]
    observed_at: AwareDatetime | None
    resolution: Resolution
    edge_count: int
    quality: DataQuality


@runtime_checkable
class EdgeFeatureAnalyzer(Protocol):
    """Contract for A01–A07 analysers (implemented from G3)."""

    analyzer_id: str
    field: str

    def compute(self, graph: PedestrianGraph) -> FeatureLayer: ...


def _is_unset(edge: RoadEdge, field: str) -> bool:
    value = getattr(edge, field)
    if field in INDICATOR_FIELDS:
        return value is None
    return value in (Passability.UNKNOWN, ClosureStatus.UNKNOWN)


def _merge_observed(current: Any, new: Any) -> Any:
    # Conservative freshness: an edge is only as fresh as its oldest input.
    if current is None:
        return new
    if new is None:
        return current
    return min(current, new)


def apply_feature_layers(
    graph: PedestrianGraph, layers: Iterable[FeatureLayer]
) -> tuple[PedestrianGraph, tuple[FeatureProvenance, ...]]:
    layers = list(layers)
    fields = [layer.field for layer in layers]
    duplicated = sorted({f for f in fields if fields.count(f) > 1})
    if duplicated:
        raise FeatureConflictError(f"multiple layers for the same field: {duplicated}")

    payloads: dict[str, dict[str, Any]] = {
        e.edge_id: e.model_dump(mode="python") for e in graph.edges()
    }
    provenance = []
    for layer in layers:
        unknown = set(layer.values) - graph.edge_ids
        if unknown:
            raise UnknownEdgeError(sorted(unknown))
        for edge_id, value in layer.values.items():
            original = graph.edge(edge_id)
            if not _is_unset(original, layer.field):
                raise FeatureConflictError(
                    f"edge {edge_id!r} already has {layer.field}={getattr(original, layer.field)!r}"
                )
            payload = payloads[edge_id]
            payload[layer.field] = value
            payload["source_ids"] = tuple(payload["source_ids"]) + layer.source_ids
            payload["observed_at"] = _merge_observed(payload["observed_at"], layer.observed_at)
            quality = dict(payload["quality"])
            quality["caveats"] = tuple(dict.fromkeys((*quality["caveats"], *layer.caveats)))
            payload["quality"] = quality
        provenance.append(
            FeatureProvenance(
                field=layer.field,
                analyzer_id=layer.analyzer_id,
                source_ids=layer.source_ids,
                observed_at=layer.observed_at,
                resolution=layer.resolution,
                edge_count=len(layer.values),
                quality=layer.quality,
            )
        )
    merged = [RoadEdge.model_validate(p) for p in payloads.values()]
    return graph.with_edges(merged), tuple(provenance)
