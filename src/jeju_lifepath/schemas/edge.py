"""공통 도로구간 데이터 계약 (REQUIREMENTS §3)."""

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# null = 미확보. 위험도 0으로 취급하지 않는다.
Index = Annotated[float, Field(ge=0, le=1)] | None


class Accessibility(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"


class ClosureStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class Resolution(BaseModel):
    spatial: str | None = None
    temporal: str | None = None


class Quality(BaseModel):
    coverage: Annotated[float, Field(ge=0, le=1)] | None = None
    caveats: list[str] = []


class RoadEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    edge_id: str = Field(min_length=1)
    source_node: str
    target_node: str
    # (lon, lat) WGS84 좌표열.
    # shortcut: shapely 없이 좌표 리스트로 보관, 공간연산 필요해지면(G2) LineString으로 교체
    geometry: list[tuple[float, float]] = Field(min_length=2)
    length_m: float = Field(gt=0)
    cctv_index: Index = None
    lighting_index: Index = None
    crime_context: Index = None
    activity_index: Index = None
    traffic_exposure: Index = None
    weather_exposure: Index = None
    accessibility: Accessibility = Accessibility.UNKNOWN
    closure_status: ClosureStatus = ClosureStatus.UNKNOWN
    observed_at: datetime | None = None
    source_ids: list[str] = Field(min_length=1)
    resolution: Resolution = Resolution()
    quality: Quality = Quality()
