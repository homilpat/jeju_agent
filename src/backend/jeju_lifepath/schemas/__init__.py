"""Shared data contracts (REQUIREMENTS §3, ARCHITECTURE §4)."""

from jeju_lifepath.schemas.common import (
    CRS_WGS84,
    ClosureStatus,
    GeoPoint,
    MobilityProfile,
    Passability,
    RoutePreference,
    RouteStatus,
)
from jeju_lifepath.schemas.edge import (
    FEATURE_FIELDS,
    INDICATOR_FIELDS,
    STATUS_FIELDS,
    Quality,
    Resolution,
    RoadEdge,
)
from jeju_lifepath.schemas.feature import FeatureValue
from jeju_lifepath.schemas.poi import POI
from jeju_lifepath.schemas.request import DETOUR_RATIO_POLICY_MAX, RouteRequest
from jeju_lifepath.schemas.result import RouteResult
from jeju_lifepath.schemas.source import (
    Confidence,
    DataOrigin,
    DataQuality,
    FeedState,
    FeedStatus,
    ImplementationStatus,
    SourceMetadata,
    VerificationStatus,
)

__all__ = [
    "CRS_WGS84",
    "DETOUR_RATIO_POLICY_MAX",
    "FEATURE_FIELDS",
    "INDICATOR_FIELDS",
    "POI",
    "STATUS_FIELDS",
    "ClosureStatus",
    "Confidence",
    "DataOrigin",
    "DataQuality",
    "FeatureValue",
    "FeedState",
    "FeedStatus",
    "GeoPoint",
    "ImplementationStatus",
    "MobilityProfile",
    "Passability",
    "Quality",
    "Resolution",
    "RoadEdge",
    "RoutePreference",
    "RouteRequest",
    "RouteResult",
    "RouteStatus",
    "SourceMetadata",
    "VerificationStatus",
]
