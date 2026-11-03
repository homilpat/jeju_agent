"""POI contract shared by C01–C03, D01 and E01 (one category-filtered system)."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field, field_validator

from jeju_lifepath.schemas.common import FrozenModel, GeoPointInput, NonEmptyStr
from jeju_lifepath.schemas.source import DataQuality, SourceIds


class POI(FrozenModel):
    poi_id: NonEmptyStr
    category: NonEmptyStr
    name: NonEmptyStr | None = None
    location: GeoPointInput
    source_ids: SourceIds
    attributes: Mapping[NonEmptyStr, str] = Field(default_factory=dict)
    quality: DataQuality = DataQuality()

    @field_validator("category")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()
