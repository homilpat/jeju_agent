"""Data registry (REQUIREMENTS §3): one record per ingested source file.

Each record stores 원본명(title), 발행기관(publisher), URL, 수집일(collected_on),
기준일(reference_date), 라이선스(license), 원본 해시(raw_sha256), 컬럼 매핑(columns),
좌표계(crs) and 결측률(stats.missing_rates), plus ingest statistics.

Records are *claims backed by files*: ``evaluate_g1_gate`` re-hashes the files
on disk, so a registry entry without the matching raw file never counts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AnyUrl, AwareDatetime, Field, field_validator, model_validator
from pyproj import CRS
from pyproj.exceptions import CRSError

from jeju_lifepath.schemas.common import FrozenModel, NonEmptyStr, UnitInterval
from jeju_lifepath.schemas.source import OFFICIAL_ORIGINS, DataOrigin

__all__ = [
    "OFFICIAL_ORIGINS",
    "DataOrigin",
    "DataRegistry",
    "DatasetRecord",
    "DatasetSpec",
    "G1GateReport",
    "IngestStats",
    "PointColumnMapping",
    "evaluate_g1_gate",
    "sha256_file",
]

REGISTRY_SCHEMA_VERSION = 1
DEFAULT_MIN_IN_REGION_RATIO = 0.95

DatasetId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_\-]{2,63}$")]


class PointColumnMapping(FrozenModel):
    """Source column names. For geographic CRSs x is longitude and y is latitude."""

    x_column: NonEmptyStr
    y_column: NonEmptyStr
    id_column: NonEmptyStr | None = None
    # canonical attribute name -> source column name
    attributes: Mapping[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)

    def source_columns(self) -> tuple[str, ...]:
        cols = [self.x_column, self.y_column]
        if self.id_column:
            cols.append(self.id_column)
        cols.extend(self.attributes.values())
        return tuple(dict.fromkeys(cols))


class DatasetSpec(FrozenModel):
    """Human-authored metadata for one source file (input to ingest)."""

    dataset_id: DatasetId
    title: NonEmptyStr
    publisher: NonEmptyStr
    source_url: AnyUrl
    license: NonEmptyStr
    collected_on: date
    reference_date: date | None = None
    origin: DataOrigin
    kind: Literal["points"] = "points"
    crs: NonEmptyStr
    encoding: NonEmptyStr | None = None
    columns: PointColumnMapping
    notes: tuple[NonEmptyStr, ...] = ()

    @field_validator("crs")
    @classmethod
    def _valid_crs(cls, value: str) -> str:
        try:
            crs = CRS.from_user_input(value)
        except CRSError as exc:
            raise ValueError(f"unknown CRS {value!r}: {exc}") from exc
        authority = crs.to_authority()
        return f"{authority[0]}:{authority[1]}" if authority else value

    @model_validator(mode="after")
    def _official_needs_https(self) -> DatasetSpec:
        if self.origin in OFFICIAL_ORIGINS and self.source_url.scheme != "https":
            raise ValueError("official datasets need an https source_url")
        if self.reference_date and self.reference_date > self.collected_on:
            raise ValueError("reference_date cannot be after collected_on")
        return self


class IngestStats(FrozenModel):
    row_count: int = Field(ge=0)
    valid_count: int = Field(ge=0)
    rejected: Mapping[NonEmptyStr, int] = Field(default_factory=dict)
    missing_rates: Mapping[NonEmptyStr, UnitInterval] = Field(default_factory=dict)
    in_region_ratio: UnitInterval | None = None
    region: NonEmptyStr | None = None
    encoding_used: NonEmptyStr

    @model_validator(mode="after")
    def _counts_consistent(self) -> IngestStats:
        if self.valid_count + sum(self.rejected.values()) != self.row_count:
            raise ValueError("valid_count + rejected must equal row_count")
        return self


class DatasetRecord(FrozenModel):
    spec: DatasetSpec
    raw_file: NonEmptyStr  # path relative to the registry directory
    raw_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    processed_file: NonEmptyStr
    processed_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    ingested_at: AwareDatetime
    stats: IngestStats


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DataRegistry:
    """JSON-file backed registry. Paths inside records are relative to ``base_dir``."""

    def __init__(self, path: Path, records: Iterable[DatasetRecord] = ()) -> None:
        self.path = Path(path)
        self._records: dict[str, DatasetRecord] = {}
        for record in records:
            self.upsert(record)

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    @classmethod
    def load(cls, path: Path) -> DataRegistry:
        path = Path(path)
        if not path.exists():
            return cls(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = payload.get("schema_version")
        if version != REGISTRY_SCHEMA_VERSION:
            raise ValueError(f"unsupported registry schema_version {version!r}")
        return cls(path, (DatasetRecord.model_validate(r) for r in payload["records"]))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "records": [r.model_dump(mode="json") for r in self.records()],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.path.write_text(text, encoding="utf-8")

    def upsert(self, record: DatasetRecord) -> None:
        self._records[record.spec.dataset_id] = record

    def get(self, dataset_id: str) -> DatasetRecord:
        return self._records[dataset_id]

    def records(self) -> list[DatasetRecord]:
        return [self._records[k] for k in sorted(self._records)]

    def resolve(self, relative: str) -> Path:
        return self.base_dir / relative


@dataclass(frozen=True)
class G1GateReport:
    passed: bool
    qualifying_dataset_ids: tuple[str, ...]
    failures: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    summary: str = ""


def check_record(
    registry: DataRegistry,
    record: DatasetRecord,
    min_in_region_ratio: float = DEFAULT_MIN_IN_REGION_RATIO,
) -> tuple[str, ...]:
    """Reasons this record does NOT satisfy the G1 evidence gate (empty = OK)."""
    reasons: list[str] = []
    if record.spec.origin not in OFFICIAL_ORIGINS:
        reasons.append(f"origin {record.spec.origin} is not an official source")
    for label, rel, expected in (
        ("raw", record.raw_file, record.raw_sha256),
        ("processed", record.processed_file, record.processed_sha256),
    ):
        path = registry.resolve(rel)
        if not path.is_file():
            reasons.append(f"{label} file missing: {rel}")
        elif sha256_file(path) != expected:
            reasons.append(f"{label} file hash mismatch: {rel}")
    if record.stats.valid_count == 0:
        reasons.append("no valid rows parsed")
    ratio = record.stats.in_region_ratio
    if ratio is None or ratio < min_in_region_ratio:
        reasons.append(f"in-region ratio {ratio} below {min_in_region_ratio}")
    return tuple(reasons)


def evaluate_g1_gate(
    registry: DataRegistry, min_in_region_ratio: float = DEFAULT_MIN_IN_REGION_RATIO
) -> G1GateReport:
    """G1 passes when ≥1 official dataset is on disk, hash-verified and parsed."""
    failures: dict[str, tuple[str, ...]] = {}
    qualifying: list[str] = []
    for record in registry.records():
        reasons = check_record(registry, record, min_in_region_ratio)
        if reasons:
            failures[record.spec.dataset_id] = reasons
        else:
            qualifying.append(record.spec.dataset_id)
    if not registry.records():
        summary = "G1 NOT PASSED: no dataset registered"
    elif qualifying:
        summary = f"G1 PASSED: verified official datasets {qualifying}"
    else:
        summary = "G1 NOT PASSED: no registered dataset satisfies the official-data evidence gate"
    return G1GateReport(bool(qualifying), tuple(qualifying), failures, summary)
