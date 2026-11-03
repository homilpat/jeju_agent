"""G1 data-pipeline tests. All CSVs here are SYNTHETIC, generated in tmp_path.

Where a test labels a synthetic file as OFFICIAL_* it does so only to exercise
the gate logic inside an isolated temporary registry; nothing is written to the
project's real registry.
"""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError
from pyproj import Transformer

from jeju_lifepath.data.connectors import SourceFormatError, read_csv_table
from jeju_lifepath.data.ingest import ingest_point_dataset, main
from jeju_lifepath.data.normalize import RejectReason, normalize_points
from jeju_lifepath.data.registry import (
    DataOrigin,
    DataRegistry,
    DatasetSpec,
    PointColumnMapping,
    evaluate_g1_gate,
)
from jeju_lifepath.geo.regions import JEJU_PROVINCE

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
MAPPING = PointColumnMapping(
    x_column="경도", y_column="위도", id_column="관리번호", attributes={"name": "시설명"}
)
HEADER = "관리번호,시설명,위도,경도\n"
GOOD_ROWS = "P1,가,33.4996,126.5312\nP2,나,33.2541,126.5600\n"


def _spec(**over):
    base = {
        "dataset_id": "synthetic-points",
        "title": "SYNTHETIC test points",
        "publisher": "test fixture",
        "source_url": "https://example.invalid/synthetic",
        "license": "test-only",
        "collected_on": date(2026, 11, 3),
        "origin": DataOrigin.SYNTHETIC,
        "crs": "EPSG:4326",
        "columns": MAPPING,
    }
    base.update(over)
    return DatasetSpec(**base)


def _write(tmp_path, text, encoding="utf-8", name="pts.csv"):
    raw = tmp_path / "raw" / name
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(text.encode(encoding))
    return raw


def _registry(tmp_path):
    return DataRegistry(tmp_path / "registry.json")


# ------------------------------------------------------------------ connectors
@pytest.mark.parametrize(
    ("encoding", "expected"),
    [("utf-8", "utf-8-sig"), ("utf-8-sig", "utf-8-sig"), ("cp949", "cp949")],
)
def test_csv_encoding_detection(tmp_path, encoding, expected):
    table = read_csv_table(_write(tmp_path, HEADER + GOOD_ROWS, encoding))
    assert table.encoding == expected
    assert table.header == ("관리번호", "시설명", "위도", "경도")
    assert table.rows[0]["시설명"] == "가"
    assert "﻿" not in table.header[0]


def test_declared_wrong_encoding_fails_loudly(tmp_path):
    with pytest.raises(SourceFormatError):
        read_csv_table(_write(tmp_path, HEADER + GOOD_ROWS, "cp949"), encoding="utf-8")


@pytest.mark.parametrize(
    "text",
    ["", "a,a\n1,2\n", "a,b\n1,2,3\n"],
    ids=["empty", "duplicate-header", "ragged-row"],
)
def test_malformed_csv_rejected(tmp_path, text):
    with pytest.raises(SourceFormatError):
        read_csv_table(_write(tmp_path, text))


def test_blank_lines_skipped(tmp_path):
    table = read_csv_table(_write(tmp_path, HEADER + "\n" + GOOD_ROWS + ",,,\n"))
    assert len(table.rows) == 2


# ------------------------------------------------------------------- normalize
def _rows(*lines):
    return [dict(zip(("관리번호", "시설명", "위도", "경도"), ln, strict=True)) for ln in lines]


def test_normalize_wgs84_and_rejections():
    rows = _rows(
        ("P1", "가", "33.4996", "126.5312"),
        ("P2", "나", "", "126.5"),  # missing
        ("P3", "다", "abc", "126.5"),  # non numeric
        ("P4", "라", "126.5312", "33.4996"),  # swapped
        ("P5", "마", "95", "300"),  # garbage
        ("P1", "바", "33.3", "126.3"),  # duplicate id
        ("P7", "", "37.5665", "126.9780"),  # Seoul: valid but outside Jeju
    )
    res = normalize_points(rows, MAPPING, "EPSG:4326", JEJU_PROVINCE)
    assert [p.record_id for p in res.points] == ["P1", "P7"]
    assert res.rejected == {
        RejectReason.MISSING_COORDINATE: 1,
        RejectReason.NON_NUMERIC_COORDINATE: 1,
        RejectReason.SUSPECTED_AXIS_SWAP: 1,
        RejectReason.OUT_OF_CRS_RANGE: 1,
        RejectReason.DUPLICATE_ID: 1,
    }
    assert res.in_region_ratio == pytest.approx(0.5)
    assert res.missing_rates["위도"] == pytest.approx(1 / 7)
    assert res.missing_rates["시설명"] == pytest.approx(1 / 7)
    assert res.points[1].attributes == {"name": None}
    assert len(res.points) + sum(res.rejected.values()) == res.row_count


def test_projected_crs_transformed_to_wgs84():
    to_tm = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
    x, y = to_tm.transform(126.5312, 33.4996)
    rows = _rows(("P1", "가", f"{y:.3f}", f"{x:.3f}"))
    res = normalize_points(rows, MAPPING, "EPSG:5186", JEJU_PROVINCE)
    assert res.points[0].lon == pytest.approx(126.5312, abs=1e-6)
    assert res.points[0].lat == pytest.approx(33.4996, abs=1e-6)
    assert res.in_region_ratio == 1.0


def test_thousands_separator_and_generated_ids():
    mapping = PointColumnMapping(x_column="x", y_column="y")
    res = normalize_points(
        [{"x": "126.5", "y": "33.5"}, {"x": "126.6", "y": "33.4"}], mapping, "EPSG:4326"
    )
    assert [p.record_id for p in res.points] == ["row-1", "row-2"]
    assert res.in_region_ratio is None  # no region requested


def test_empty_rows():
    res = normalize_points([], MAPPING, "EPSG:4326", JEJU_PROVINCE)
    assert res.points == ()
    assert res.in_region_ratio is None
    assert res.missing_rates["위도"] == 0.0


# ------------------------------------------------------------------------ spec
def test_spec_validation():
    assert _spec(crs="epsg:5186").crs == "EPSG:5186"
    with pytest.raises(ValidationError):
        _spec(crs="EPSG:999999")
    with pytest.raises(ValidationError):
        _spec(dataset_id="Bad Id!")
    with pytest.raises(ValidationError):
        _spec(origin=DataOrigin.OFFICIAL_JEJU, source_url="http://insecure.example")
    with pytest.raises(ValidationError):
        _spec(reference_date=date(2026, 12, 1))  # after collected_on
    with pytest.raises(ValidationError):
        _spec(license=" ")


# ---------------------------------------------------------- ingest + registry
def test_ingest_records_provenance_and_round_trips(tmp_path):
    raw = _write(tmp_path, HEADER + GOOD_ROWS, "cp949")
    reg = _registry(tmp_path)
    rec = ingest_point_dataset(_spec(), raw, reg, now=NOW)
    assert rec.raw_file == "raw/pts.csv"
    assert rec.stats.encoding_used == "cp949"
    assert rec.stats.valid_count == 2
    assert rec.stats.in_region_ratio == 1.0
    assert (tmp_path / rec.processed_file).is_file()
    reg.save()
    assert DataRegistry.load(reg.path).get("synthetic-points") == rec


def test_ingest_is_deterministic(tmp_path):
    raw = _write(tmp_path, HEADER + GOOD_ROWS)
    a = ingest_point_dataset(_spec(), raw, _registry(tmp_path), now=NOW)
    b = ingest_point_dataset(_spec(), raw, _registry(tmp_path), now=NOW)
    assert a.processed_sha256 == b.processed_sha256


def test_ingest_rejects_missing_columns_and_outside_files(tmp_path):
    raw = _write(tmp_path, "id,lat\n1,33\n")
    with pytest.raises(SourceFormatError, match="not in header"):
        ingest_point_dataset(_spec(), raw, _registry(tmp_path))
    outside = tmp_path.parent / "outside.csv"
    outside.write_text(HEADER + GOOD_ROWS, encoding="utf-8")
    with pytest.raises(ValueError, match="inside the registry dir"):
        ingest_point_dataset(_spec(), outside, _registry(tmp_path))


def test_registry_rejects_unknown_schema_version(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text('{"schema_version": 99, "records": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        DataRegistry.load(path)


# ------------------------------------------------------------------- G1 gate
def test_gate_fails_without_records(tmp_path):
    report = evaluate_g1_gate(_registry(tmp_path))
    assert not report.passed
    assert "no dataset registered" in report.summary


def test_gate_rejects_synthetic_origin(tmp_path):
    reg = _registry(tmp_path)
    ingest_point_dataset(_spec(), _write(tmp_path, HEADER + GOOD_ROWS), reg, now=NOW)
    report = evaluate_g1_gate(reg)
    assert not report.passed
    assert any("not an official source" in r for r in report.failures["synthetic-points"])


def test_gate_logic_with_official_label_and_tamper_detection(tmp_path):
    # Gate-logic test only: synthetic file labelled official inside tmp registry.
    reg = _registry(tmp_path)
    raw = _write(tmp_path, HEADER + GOOD_ROWS)
    spec = _spec(dataset_id="gate-logic-check", origin=DataOrigin.OFFICIAL_JEJU)
    ingest_point_dataset(spec, raw, reg, now=NOW)
    assert evaluate_g1_gate(reg).passed

    raw.write_text(HEADER + GOOD_ROWS + "P9,x,33.3,126.3\n", encoding="utf-8")
    report = evaluate_g1_gate(reg)
    assert not report.passed
    assert any("raw file hash mismatch" in r for r in report.failures["gate-logic-check"])

    raw.unlink()
    assert any("raw file missing" in r for r in evaluate_g1_gate(reg).failures["gate-logic-check"])


def test_gate_requires_points_in_region(tmp_path):
    reg = _registry(tmp_path)
    seoul = HEADER + "S1,서울,37.5665,126.9780\n"
    spec = _spec(dataset_id="wrong-region", origin=DataOrigin.OFFICIAL_NATIONAL)
    ingest_point_dataset(spec, _write(tmp_path, seoul), reg, now=NOW)
    report = evaluate_g1_gate(reg)
    assert not report.passed
    assert any("in-region ratio" in r for r in report.failures["wrong-region"])


def test_cli_gate_and_ingest(tmp_path, capsys):
    registry_path = tmp_path / "registry.json"
    assert main(["--registry", str(registry_path), "gate"]) == 1
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(_spec().model_dump_json(), encoding="utf-8")
    raw = _write(tmp_path, HEADER + GOOD_ROWS)
    assert main(["--registry", str(registry_path), "ingest", str(spec_path), str(raw)]) == 0
    assert registry_path.is_file()
    assert main(["--registry", str(registry_path), "gate"]) == 1  # synthetic never passes
    assert "not an official source" in capsys.readouterr().out
