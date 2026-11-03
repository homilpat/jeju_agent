"""FeatureValue — a single edge feature with its full evidence context.

Analysers emit ``FeatureLayer``s (geo/features.py); ``FeatureLayer.feature_value``
expands one entry into this self-describing record for explanation/validation.
``value is None`` means *missing*, never zero.
"""

from __future__ import annotations

from pydantic import AwareDatetime

from jeju_lifepath.schemas.common import ClosureStatus, FrozenModel, NonEmptyStr, Passability
from jeju_lifepath.schemas.edge import Resolution
from jeju_lifepath.schemas.source import DataQuality, SourceIds

FeatureScalar = float | Passability | ClosureStatus | None


class FeatureValue(FrozenModel):
    edge_id: NonEmptyStr
    field: NonEmptyStr
    value: FeatureScalar
    unit: NonEmptyStr
    observed_at: AwareDatetime | None = None
    resolution: Resolution = Resolution()
    source_ids: SourceIds
    quality: DataQuality = DataQuality()

    @property
    def is_missing(self) -> bool:
        return self.value is None
