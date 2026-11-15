"""Local file connectors.

Korean public-data CSVs are commonly UTF-8 (with/without BOM) or CP949.
Decoding is strict: a file is never silently decoded with replacement chars.
Online connectors (공공데이터포털/제주데이터허브 API) are added when network
access and API keys are available; without them the system works in an explicit
local-snapshot mode (REQUIREMENTS §7).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8-sig", "cp949")


class SourceFormatError(ValueError):
    """The source file cannot be decoded or does not match the declared schema."""


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    encoding: str


def decode_bytes(
    raw: bytes, encoding: str | None, candidates: Sequence[str] = DEFAULT_ENCODING_CANDIDATES
) -> tuple[str, str]:
    tried = (encoding,) if encoding else tuple(candidates)
    errors = []
    for enc in tried:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError) as exc:
            errors.append(f"{enc}: {exc}")
    raise SourceFormatError(f"cannot decode file with {tried}: {errors}")


def read_csv_table(path: Path, encoding: str | None = None) -> Table:
    text, used = decode_bytes(Path(path).read_bytes(), encoding)
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = tuple(h.strip() for h in next(reader))
    except StopIteration:
        raise SourceFormatError(f"{path}: empty file") from None
    if len(set(header)) != len(header):
        raise SourceFormatError(f"{path}: duplicate column names in header {header}")
    rows = []
    for line_no, values in enumerate(reader, start=2):
        if not values or all(not v.strip() for v in values):
            continue
        if len(values) != len(header):
            raise SourceFormatError(
                f"{path}:{line_no}: expected {len(header)} fields, got {len(values)}"
            )
        rows.append(dict(zip(header, (v.strip() for v in values), strict=True)))
    return Table(header, tuple(rows), used)
