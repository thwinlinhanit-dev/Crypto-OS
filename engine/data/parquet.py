"""Spec section 4.4 / T1.2: deterministic, content-addressed Parquet writer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

import polars as pl

from engine.data.bars import BAR_COLUMNS, Timeframe
from engine.errors import ImmutableResultError

#: Identity fields hashed into content_sha256 (section 4.4); ingested_at is
#: ingest bookkeeping and must never affect the hash or it would break AC1.
CANONICAL_COLUMNS: tuple[str, ...] = tuple(c for c in BAR_COLUMNS if c != "ingested_at")

FLOAT_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume", "quote_volume")

#: Writer settings pinned as constants (section 4.4 "deterministic writer").
COMPRESSION: Literal["zstd"] = "zstd"
ROW_GROUP_SIZE = 8192
STATISTICS = True

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._:-]+$")


class Layer(StrEnum):
    """Storage layer: native venue bars vs. derived (resampled) bars."""

    NORMALIZED = "normalized"
    DERIVED = "derived"


@dataclass(frozen=True, slots=True)
class PartRef:
    """One stored part; ``path`` is relative to the data root with '/' separators."""

    path: str
    content_sha256: str
    rows: int


def _check_extra_columns(extra_columns: tuple[str, ...]) -> None:
    """Extra columns extend identity; they may never redefine it (AC1)."""
    if len(set(extra_columns)) != len(extra_columns):
        raise ValueError(f"extra_columns contains duplicates: {list(extra_columns)}")
    forbidden = [
        column
        for column in extra_columns
        if column in CANONICAL_COLUMNS or column == "ingested_at"
    ]
    if forbidden:
        raise ValueError(f"extra_columns may not name canonical columns: {forbidden}")


def canonical_bytes(
    frame: pl.DataFrame, extra_columns: tuple[str, ...] = ()
) -> bytes:
    """Canonical row serialization: one JSON line per row, sorted by open_time.

    Lines are ``\\n``-joined with no trailing newline. Rows are sorted first, so
    the hash is invariant to the order rows were fetched or assembled.
    ``extra_columns`` (e.g. the derived ``incomplete`` flag) join the identity:
    rows that differ there are different content (design note 7.1).
    """
    _check_extra_columns(extra_columns)
    columns = (*CANONICAL_COLUMNS, *extra_columns)
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"frame is missing canonical columns: {missing}")
    rows = frame.select(list(columns)).sort("open_time").rows(named=True)
    lines = [
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True) for row in rows
    ]
    return "\n".join(lines).encode("utf-8")


def content_sha256(frame: pl.DataFrame, extra_columns: tuple[str, ...] = ()) -> str:
    return hashlib.sha256(canonical_bytes(frame, extra_columns)).hexdigest()


def part_rel_path(
    *,
    layer: Layer,
    venue: str,
    market_type: str,
    native_symbol: str,
    timeframe: Timeframe | str,
    year: int,
    sha256: str,
) -> str:
    """Partition path (section 4.4): fixed order, content-addressed file name."""
    timeframe_value = timeframe.value if isinstance(timeframe, Timeframe) else timeframe
    components = {
        "venue": venue,
        "market_type": market_type,
        "native_symbol": native_symbol,
        "timeframe": timeframe_value,
    }
    for name, value in components.items():
        if not _SAFE_COMPONENT.match(value):
            raise ValueError(f"unsafe {name} for storage path: {value!r}")
    name = f"part-{sha256[:16]}.parquet"
    return (
        f"{layer.value}/venue={venue}/market_type={market_type}/symbol={native_symbol}"
        f"/tf={timeframe_value}/year={year}/{name}"
    )


def _checked_sorted(
    frame: pl.DataFrame, extra_columns: tuple[str, ...]
) -> tuple[pl.DataFrame, int]:
    """Sort, reject duplicates, non-finite values and mixed years (fail closed)."""
    if frame.is_empty():
        raise ValueError("refusing to write an empty frame")
    missing = [
        column for column in (*BAR_COLUMNS, *extra_columns) if column not in frame.columns
    ]
    if missing:
        raise ValueError(f"frame is missing bar columns: {missing}")
    allowed = set(BAR_COLUMNS) | set(extra_columns)
    unexpected = [column for column in frame.columns if column not in allowed]
    if unexpected:
        # Never silently drop a column: the caller would lose the data it carried.
        raise ValueError(f"frame has unexpected columns: {unexpected}")
    ordered = frame.select([*BAR_COLUMNS, *extra_columns]).sort(
        "open_time", maintain_order=True
    )
    opens = ordered["open_time"].to_list()
    if len(set(opens)) != len(opens):
        raise ValueError("duplicate open_time in frame; duplicates are a Guardian failure")
    not_finite = ordered.filter(
        ~pl.all_horizontal([pl.col(column).is_finite() for column in FLOAT_COLUMNS])
    )
    if not_finite.height > 0:
        raise ValueError("non-finite value in bar frame")
    years = {datetime.fromtimestamp(open_ms / 1000, tz=UTC).year for open_ms in opens}
    if len(years) != 1:
        raise ValueError(f"frame spans multiple partition years: {sorted(years)}")
    return ordered, years.pop()


def write_part(
    frame: pl.DataFrame,
    *,
    data_dir: Path,
    venue: str,
    market_type: str,
    native_symbol: str,
    timeframe: Timeframe | str,
    layer: Layer = Layer.NORMALIZED,
    extra_columns: tuple[str, ...] = (),
) -> PartRef:
    """Write one content-addressed part atomically and read-only.

    An existing target is refused with IMMUTABLE_RESULT: files are never
    overwritten in place (PD-7). Callers that resume an ingest check
    :func:`part_exists` and skip instead. ``extra_columns`` (design note 7.1)
    are stored *and* hashed with the rows; frames carrying other columns are
    refused rather than silently trimmed.
    """
    _check_extra_columns(extra_columns)
    ordered, year = _checked_sorted(frame, extra_columns)
    sha256 = content_sha256(ordered, extra_columns)
    rel = part_rel_path(
        layer=layer,
        venue=venue,
        market_type=market_type,
        native_symbol=native_symbol,
        timeframe=timeframe,
        year=year,
        sha256=sha256,
    )
    target = data_dir.joinpath(*rel.split("/"))
    if target.exists():
        raise ImmutableResultError(
            "content-addressed part already exists; files are read-only after write",
            {"path": rel},
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    ordered.write_parquet(
        tmp,
        compression=COMPRESSION,
        statistics=STATISTICS,
        row_group_size=ROW_GROUP_SIZE,
    )
    with open(tmp, "ab") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    target.chmod(stat.S_IREAD)
    return PartRef(path=rel, content_sha256=sha256, rows=ordered.height)


def part_exists(data_dir: Path, rel_path: str) -> bool:
    return data_dir.joinpath(*rel_path.split("/")).exists()


def read_part(data_dir: Path, rel_path: str) -> pl.DataFrame:
    return pl.read_parquet(data_dir.joinpath(*rel_path.split("/")))


def list_parts(
    data_dir: Path,
    *,
    venue: str,
    market_type: str,
    native_symbol: str,
    timeframe: Timeframe | str,
    layer: Layer = Layer.NORMALIZED,
) -> list[str]:
    """Stored parts for one partition, sorted by path (used for resume/resample)."""
    timeframe_value = timeframe.value if isinstance(timeframe, Timeframe) else timeframe
    prefix = data_dir.joinpath(
        layer.value,
        f"venue={venue}",
        f"market_type={market_type}",
        f"symbol={native_symbol}",
        f"tf={timeframe_value}",
    )
    if not prefix.exists():
        return []
    found = prefix.glob("year=*/part-*.parquet")
    rels = [
        str(path.relative_to(data_dir)).replace(os.sep, "/")
        for path in found
        if path.name.startswith("part-") and path.suffix == ".parquet"
    ]
    return sorted(rels)
