"""Provenance and quality contracts shared by every layer.

* ``SourceMetadata`` — where a value came from and whether it is verified.
* ``DataQuality``    — coverage / missingness / confidence of a value or layer.
* ``FeedStatus``     — liveness of an external feed (closures, weather, POI APIs).
  An unreachable feed is ``UNAVAILABLE`` with its last verified time; it is never
  presented as live (REQUIREMENTS §7).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import AnyUrl, AwareDatetime, Field, model_validator

from jeju_lifepath.schemas.common import FrozenModel, NonEmptyStr, UnitInterval

SYNTHETIC_PREFIX = "synthetic:"


class DataOrigin(StrEnum):
    OFFICIAL_JEJU = "OFFICIAL_JEJU"  # 제주특별자치도·제주데이터허브 등 제주 공식 기관
    OFFICIAL_NATIONAL = "OFFICIAL_NATIONAL"  # 공공데이터포털 등 국가 공식 기관
    COMMUNITY = "COMMUNITY"  # OpenStreetMap 등 공개 커뮤니티 데이터
    SYNTHETIC = "SYNTHETIC"  # 테스트/시연용 가상 데이터 (DEMO 전용)


OFFICIAL_ORIGINS = frozenset({DataOrigin.OFFICIAL_JEJU, DataOrigin.OFFICIAL_NATIONAL})


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"  # 원문·라이선스·기준일 확인 및 해시 검증 완료
    UNVERIFIED = "UNVERIFIED"  # 출처는 있으나 확인 미완료
    SYNTHETIC = "SYNTHETIC"  # 가상 데이터


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class ImplementationStatus(StrEnum):
    """Status labels used in docs and in runtime capability reports."""

    IMPLEMENTED_WITH_SYNTHETIC_FIXTURES = "IMPLEMENTED_WITH_SYNTHETIC_FIXTURES"
    PENDING_REAL_DATA = "PENDING_REAL_DATA"
    VERIFIED_WITH_REAL_DATA = "VERIFIED_WITH_REAL_DATA"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


def is_synthetic_source(source_id: str) -> bool:
    return source_id.startswith(SYNTHETIC_PREFIX)


class SourceMetadata(FrozenModel):
    source_id: NonEmptyStr
    title: NonEmptyStr
    publisher: NonEmptyStr
    url: AnyUrl | None = None
    license: NonEmptyStr
    origin: DataOrigin
    reference_date: date | None = None
    retrieved_at: AwareDatetime | None = None
    verification: VerificationStatus

    @model_validator(mode="after")
    def _synthetic_is_explicit(self) -> SourceMetadata:
        synthetic_flags = {
            self.origin is DataOrigin.SYNTHETIC,
            self.verification is VerificationStatus.SYNTHETIC,
            is_synthetic_source(self.source_id),
        }
        if len(synthetic_flags) != 1:
            raise ValueError(
                "synthetic data must be marked consistently: origin=SYNTHETIC, "
                f"verification=SYNTHETIC and source_id prefix {SYNTHETIC_PREFIX!r}"
            )
        if self.origin in OFFICIAL_ORIGINS and self.url is None:
            raise ValueError("official sources need a URL")
        return self


class DataQuality(FrozenModel):
    coverage: UnitInterval | None = None
    missing_rate: UnitInterval | None = None
    confidence: Confidence = Confidence.UNKNOWN
    caveats: tuple[NonEmptyStr, ...] = ()


class FeedState(StrEnum):
    AVAILABLE = "AVAILABLE"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class FeedStatus(FrozenModel):
    """State of an external feed at ``checked_at``."""

    name: NonEmptyStr
    state: FeedState
    checked_at: AwareDatetime
    last_verified_at: AwareDatetime | None = None
    detail: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _consistent(self) -> FeedStatus:
        if self.state is FeedState.AVAILABLE and self.last_verified_at is None:
            raise ValueError("AVAILABLE feed needs last_verified_at")
        if self.last_verified_at and self.last_verified_at > self.checked_at:
            raise ValueError("last_verified_at cannot be after checked_at")
        return self

    @classmethod
    def evaluate(
        cls,
        name: str,
        last_verified_at: datetime | None,
        now: datetime,
        max_age: timedelta,
        detail: str | None = None,
    ) -> FeedStatus:
        """Derive the state from the last successful verification time."""
        if last_verified_at is None:
            state = FeedState.UNAVAILABLE
        elif now - last_verified_at > max_age:
            state = FeedState.STALE
        else:
            state = FeedState.AVAILABLE
        return cls(
            name=name,
            state=state,
            checked_at=now,
            last_verified_at=last_verified_at,
            detail=detail,
        )

    @property
    def is_usable(self) -> bool:
        return self.state is FeedState.AVAILABLE


SourceIds = Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
