"""Spec section 4.4: the dataset manifest and the dataset_version hash.

The manifest answers the section 4.4 question - "what exactly did this use?" -
and ``dataset_version`` is its reproducible identity (PD-6): sha256 over the
canonical JSON of the four version fields plus every source file's path,
content hash and row count. Scope and quality fields are stored alongside but
are *not* hashed: they are assessments of the same immutable bytes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from engine.versions import DATASET_SCHEMA_VERSION

#: Section 4.4 values, verbatim from the spec.
BAR_SEMANTICS_VERSION = "closed-bars-utc-v1"
TIMEZONE_POLICY_VERSION = "utc-v1"

#: ASSUMPTION (design note, carried as an open question): the spec names this
#: field but gives no vocabulary. It versions the tick/lot/listed_at metadata
#: that came with the bars, taken from the venue's exchangeInfo in M1.
INSTRUMENT_METADATA_VERSION = "binance-spot-exchangeinfo-v1"


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One entry of the manifest's ``source_files`` (section 4.4)."""

    path: str
    content_sha256: str
    rows: int

    @classmethod
    def from_entry(cls, entry: SourceFile | Mapping[str, Any]) -> SourceFile:
        if isinstance(entry, SourceFile):
            return entry
        return cls(
            path=str(entry["path"]),
            content_sha256=str(entry["content_sha256"]),
            rows=int(entry["rows"]),
        )

    def to_entry(self) -> dict[str, Any]:
        return {"path": self.path, "content_sha256": self.content_sha256, "rows": self.rows}


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """Stored row of ``market.dataset_manifest`` (section 10) as a value object."""

    dataset_version: str
    venue: str
    market_type: str
    instruments: tuple[str, ...]
    timeframes: tuple[str, ...]
    start_time: datetime
    end_time: datetime
    source_files: tuple[SourceFile, ...]
    row_counts: dict[str, int]
    missing_ranges: list[dict[str, Any]]
    quality_summary: dict[str, Any]
    dataset_schema_version: str
    bar_semantics_version: str
    timezone_policy_version: str
    instrument_metadata_version: str
    created_at: datetime


def dataset_version(
    source_files: Iterable[SourceFile | Mapping[str, Any]],
    *,
    dataset_schema_version: str,
    bar_semantics_version: str,
    timezone_policy_version: str,
    instrument_metadata_version: str,
) -> str:
    """sha256 over the canonical JSON of the identity inputs (section 4.4)."""
    entries = sorted(
        (SourceFile.from_entry(entry).to_entry() for entry in source_files),
        key=lambda entry: entry["path"],
    )
    payload = {
        "source_files": entries,
        "dataset_schema_version": dataset_schema_version,
        "bar_semantics_version": bar_semantics_version,
        "timezone_policy_version": timezone_policy_version,
        "instrument_metadata_version": instrument_metadata_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_manifest(
    *,
    venue: str,
    market_type: str,
    instruments: Iterable[UUID | str],
    timeframes: Iterable[str],
    start_time: datetime,
    end_time: datetime,
    source_files: Iterable[SourceFile | Mapping[str, Any]],
    row_counts: Mapping[str, int],
    missing_ranges: Iterable[Mapping[str, Any]],
    quality_summary: Mapping[str, Any],
    dataset_schema_version: str = DATASET_SCHEMA_VERSION,
    bar_semantics_version: str = BAR_SEMANTICS_VERSION,
    timezone_policy_version: str = TIMEZONE_POLICY_VERSION,
    instrument_metadata_version: str = INSTRUMENT_METADATA_VERSION,
    created_at: datetime | None = None,
) -> DatasetManifest:
    """Assemble a manifest and compute its dataset_version."""
    for name, value in (("start_time", start_time), ("end_time", end_time)):
        if value.tzinfo is None:
            raise ValueError(f"{name} must be timezone-aware UTC; naive timestamps are refused")
    files = tuple(SourceFile.from_entry(entry) for entry in source_files)
    version = dataset_version(
        files,
        dataset_schema_version=dataset_schema_version,
        bar_semantics_version=bar_semantics_version,
        timezone_policy_version=timezone_policy_version,
        instrument_metadata_version=instrument_metadata_version,
    )
    return DatasetManifest(
        dataset_version=version,
        venue=venue,
        market_type=market_type,
        instruments=tuple(str(item) for item in instruments),
        timeframes=tuple(str(item) for item in timeframes),
        start_time=start_time,
        end_time=end_time,
        source_files=files,
        row_counts={str(key): int(value) for key, value in row_counts.items()},
        missing_ranges=[dict(entry) for entry in missing_ranges],
        quality_summary=dict(quality_summary),
        dataset_schema_version=dataset_schema_version,
        bar_semantics_version=bar_semantics_version,
        timezone_policy_version=timezone_policy_version,
        instrument_metadata_version=instrument_metadata_version,
        created_at=created_at if created_at is not None else datetime.now(UTC),
    )


_SELECT = (
    "dataset_version, venue, market_type, instruments, timeframes, start_time, end_time, "
    "source_files, row_counts, missing_ranges, quality_summary, dataset_schema_version, "
    "bar_semantics_version, timezone_policy_version, instrument_metadata_version, created_at"
)


def save_manifest(conn: Connection, manifest: DatasetManifest) -> None:
    """Store the manifest; re-storing an identical dataset_version is idempotent.

    ``created_at`` keeps the first registration time; everything else refreshes,
    because the assessment fields (quality, missing_ranges) may be recomputed
    for the same immutable bytes.
    """
    statement = text(
        f"INSERT INTO market.dataset_manifest ({_SELECT}) VALUES ("
        ":dataset_version, :venue, :market_type, "
        "CAST(:instruments AS jsonb), CAST(:timeframes AS jsonb), "
        ":start_time, :end_time, CAST(:source_files AS jsonb), "
        "CAST(:row_counts AS jsonb), CAST(:missing_ranges AS jsonb), "
        "CAST(:quality_summary AS jsonb), :dataset_schema_version, "
        ":bar_semantics_version, :timezone_policy_version, "
        ":instrument_metadata_version, :created_at) "
        "ON CONFLICT (dataset_version) DO UPDATE SET "
        "venue = EXCLUDED.venue, market_type = EXCLUDED.market_type, "
        "instruments = EXCLUDED.instruments, timeframes = EXCLUDED.timeframes, "
        "start_time = EXCLUDED.start_time, end_time = EXCLUDED.end_time, "
        "source_files = EXCLUDED.source_files, row_counts = EXCLUDED.row_counts, "
        "missing_ranges = EXCLUDED.missing_ranges, "
        "quality_summary = EXCLUDED.quality_summary, "
        "dataset_schema_version = EXCLUDED.dataset_schema_version, "
        "bar_semantics_version = EXCLUDED.bar_semantics_version, "
        "timezone_policy_version = EXCLUDED.timezone_policy_version, "
        "instrument_metadata_version = EXCLUDED.instrument_metadata_version"
    )
    conn.execute(
        statement,
        {
            "dataset_version": manifest.dataset_version,
            "venue": manifest.venue,
            "market_type": manifest.market_type,
            "instruments": json.dumps(list(manifest.instruments)),
            "timeframes": json.dumps(list(manifest.timeframes)),
            "start_time": manifest.start_time,
            "end_time": manifest.end_time,
            "source_files": json.dumps([entry.to_entry() for entry in manifest.source_files]),
            "row_counts": json.dumps(manifest.row_counts),
            "missing_ranges": json.dumps(manifest.missing_ranges),
            "quality_summary": json.dumps(manifest.quality_summary),
            "dataset_schema_version": manifest.dataset_schema_version,
            "bar_semantics_version": manifest.bar_semantics_version,
            "timezone_policy_version": manifest.timezone_policy_version,
            "instrument_metadata_version": manifest.instrument_metadata_version,
            "created_at": manifest.created_at,
        },
    )


def get_manifest(conn: Connection, dataset_version: str) -> DatasetManifest | None:
    row = conn.execute(
        text(f"SELECT {_SELECT} FROM market.dataset_manifest WHERE dataset_version = :version"),
        {"version": dataset_version},
    ).first()
    if row is None:
        return None
    return DatasetManifest(
        dataset_version=row.dataset_version,
        venue=row.venue,
        market_type=row.market_type,
        instruments=tuple(row.instruments),
        timeframes=tuple(row.timeframes),
        start_time=row.start_time,
        end_time=row.end_time,
        source_files=tuple(SourceFile.from_entry(entry) for entry in row.source_files),
        row_counts={str(key): int(value) for key, value in row.row_counts.items()},
        missing_ranges=list(row.missing_ranges),
        quality_summary=dict(row.quality_summary),
        dataset_schema_version=row.dataset_schema_version,
        bar_semantics_version=row.bar_semantics_version,
        timezone_policy_version=row.timezone_policy_version,
        instrument_metadata_version=row.instrument_metadata_version,
        created_at=row.created_at,
    )
