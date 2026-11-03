from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from jeju_lifepath.schemas import (
    POI,
    DataOrigin,
    DataQuality,
    FeedState,
    FeedStatus,
    SourceMetadata,
    VerificationStatus,
)

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


def _meta(**over):
    base = {
        "source_id": "synthetic:demo",
        "title": "demo",
        "publisher": "test",
        "license": "test-only",
        "origin": DataOrigin.SYNTHETIC,
        "verification": VerificationStatus.SYNTHETIC,
    }
    base.update(over)
    return SourceMetadata(**base)


class TestSourceMetadata:
    def test_synthetic_consistent(self):
        assert _meta().origin is DataOrigin.SYNTHETIC

    @pytest.mark.parametrize(
        "over",
        [
            {"source_id": "jeju:cctv"},  # synthetic data without prefix
            {"verification": VerificationStatus.VERIFIED},
            {"origin": DataOrigin.OFFICIAL_JEJU, "url": "https://x.example"},
        ],
    )
    def test_synthetic_must_be_marked_consistently(self, over):
        with pytest.raises(ValidationError):
            _meta(**over)

    def test_official_requires_url(self):
        common = {
            "source_id": "jeju:cctv",
            "origin": DataOrigin.OFFICIAL_JEJU,
            "verification": VerificationStatus.UNVERIFIED,
        }
        with pytest.raises(ValidationError):
            _meta(**common)
        assert _meta(**common, url="https://data.example/cctv").url is not None


class TestFeedStatus:
    @pytest.mark.parametrize(
        ("last", "state"),
        [
            (None, FeedState.UNAVAILABLE),
            (NOW - timedelta(minutes=5), FeedState.AVAILABLE),
            (NOW - timedelta(hours=2), FeedState.STALE),
            (NOW - timedelta(hours=1), FeedState.AVAILABLE),  # boundary: exactly max_age
        ],
    )
    def test_evaluate(self, last, state):
        status = FeedStatus.evaluate("closures", last, NOW, timedelta(hours=1))
        assert status.state is state
        assert status.is_usable is (state is FeedState.AVAILABLE)

    def test_invariants(self):
        with pytest.raises(ValidationError):
            FeedStatus(name="x", state=FeedState.AVAILABLE, checked_at=NOW)
        with pytest.raises(ValidationError):
            FeedStatus(
                name="x",
                state=FeedState.STALE,
                checked_at=NOW,
                last_verified_at=NOW + timedelta(seconds=1),
            )


def test_data_quality_ranges():
    assert DataQuality(coverage=1.0, missing_rate=0.0).coverage == 1.0
    with pytest.raises(ValidationError):
        DataQuality(missing_rate=1.5)


def test_poi_category_normalised_and_provenance_required():
    poi = POI(
        poi_id="p1",
        category="Pharmacy",
        location=(33.5, 126.53),
        source_ids=["synthetic:poi"],
    )
    assert poi.category == "pharmacy"
    with pytest.raises(ValidationError):
        POI(poi_id="p1", category="toilet", location=(33.5, 126.53), source_ids=[])
