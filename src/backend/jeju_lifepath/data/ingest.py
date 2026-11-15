"""Ingest a local source file described by a DatasetSpec into the registry.

Usage::

    python -m jeju_lifepath.data.ingest ingest SPEC.json RAW.csv [--registry data/registry.json]
    python -m jeju_lifepath.data.ingest gate [--registry data/registry.json]

The raw file must already live under the registry directory (e.g. data/raw/);
the registry stores paths relative to it so evidence stays portable.
Processed output is a deterministic GeoJSON (same input -> same bytes -> same hash).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from jeju_lifepath.data.connectors import SourceFormatError, read_csv_table
from jeju_lifepath.data.normalize import PointNormalization, normalize_points
from jeju_lifepath.data.registry import (
    DataRegistry,
    DatasetRecord,
    DatasetSpec,
    IngestStats,
    evaluate_g1_gate,
    sha256_file,
)
from jeju_lifepath.geo.regions import JEJU_PROVINCE, RegionEnvelope

DEFAULT_REGISTRY = Path("data/registry.json")
PROCESSED_DIRNAME = "processed"


def to_geojson(spec: DatasetSpec, result: PointNormalization) -> str:
    features = [
        {
            "type": "Feature",
            "id": p.record_id,
            "geometry": {"type": "Point", "coordinates": [round(p.lon, 7), round(p.lat, 7)]},
            "properties": {"dataset_id": spec.dataset_id, **p.attributes},
        }
        for p in result.points
    ]
    payload = {
        "type": "FeatureCollection",
        "crs_note": "WGS84 EPSG:4326 (lon, lat)",
        "dataset_id": spec.dataset_id,
        "origin": spec.origin,
        "features": features,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def ingest_point_dataset(
    spec: DatasetSpec,
    raw_path: Path,
    registry: DataRegistry,
    region: RegionEnvelope = JEJU_PROVINCE,
    now: datetime | None = None,
) -> DatasetRecord:
    raw_path = Path(raw_path).resolve()
    base = registry.base_dir.resolve()
    try:
        raw_rel = raw_path.relative_to(base).as_posix()
    except ValueError:
        raise ValueError(f"raw file {raw_path} must be inside the registry dir {base}") from None

    table = read_csv_table(raw_path, spec.encoding)
    missing_cols = [c for c in spec.columns.source_columns() if c not in table.header]
    if missing_cols:
        raise SourceFormatError(f"columns {missing_cols} not in header {list(table.header)}")

    result = normalize_points(table.rows, spec.columns, spec.crs, region)
    processed_rel = f"{PROCESSED_DIRNAME}/{spec.dataset_id}.geojson"
    processed_path = registry.resolve(processed_rel)
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    processed_path.write_text(to_geojson(spec, result), encoding="utf-8")

    record = DatasetRecord(
        spec=spec,
        raw_file=raw_rel,
        raw_sha256=sha256_file(raw_path),
        processed_file=processed_rel,
        processed_sha256=sha256_file(processed_path),
        ingested_at=now or datetime.now(UTC),
        stats=IngestStats(
            row_count=result.row_count,
            valid_count=len(result.points),
            rejected={str(k): v for k, v in result.rejected.items()},
            missing_rates=dict(result.missing_rates),
            in_region_ratio=result.in_region_ratio,
            region=region.name,
            encoding_used=table.encoding,
        ),
    )
    registry.upsert(record)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jeju_lifepath.data.ingest")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    sub = parser.add_subparsers(dest="command", required=True)
    p_ingest = sub.add_parser("ingest", help="ingest one local file")
    p_ingest.add_argument("spec", type=Path)
    p_ingest.add_argument("raw", type=Path)
    sub.add_parser("gate", help="evaluate the G1 official-data evidence gate")
    args = parser.parse_args(argv)

    registry = DataRegistry.load(args.registry)
    if args.command == "ingest":
        spec = DatasetSpec.model_validate_json(args.spec.read_text(encoding="utf-8"))
        record = ingest_point_dataset(spec, args.raw, registry)
        registry.save()
        print(record.stats.model_dump_json(indent=2))
        return 0

    report = evaluate_g1_gate(registry)
    print(report.summary)
    for dataset_id, reasons in report.failures.items():
        for reason in reasons:
            print(f"  - {dataset_id}: {reason}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
