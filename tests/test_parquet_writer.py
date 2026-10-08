"""Spec section 4.4 / T1.2: the deterministic content-addressed writer.

Golden hashes are frozen here (section 14.1): changing one needs a PR note.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from engine.data.bars import BAR_COLUMNS, bar_frame
from engine.data.instruments import MarketType, instrument_id_for
from engine.data.parquet import (
    CANONICAL_COLUMNS,
    Layer,
    canonical_bytes,
    content_sha256,
    list_parts,
    part_exists,
    part_rel_path,
    read_part,
    write_part,
)
from engine.errors import ImmutableResultError

DAY_MS = 86_400_000
MONDAY_MS = 1_704_067_200_000  # 2024-01-01 00:00 UTC
INGESTED_AT_MS = 1_710_000_000_000

INSTRUMENT_ID = str(instrument_id_for("BINANCE", MarketType.SPOT, "BTCUSDT"))

#: Frozen content hash for :func:`_frame` (section 14.1, PR note to change).
GOLDEN_CONTENT_SHA256 = "8ffe35260d16391fb77cab3510150eba02905950178f5d0ab0aaf40d5537f6de"


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "instrument_id": INSTRUMENT_ID,
            "timeframe": "1D",
            "open_time": MONDAY_MS + offset * DAY_MS,
            "close_time": MONDAY_MS + (offset + 1) * DAY_MS,
            "open": 42000.0 + offset,
            "high": 43000.0 + offset,
            "low": 41000.0 + offset,
            "close": 42500.0 + offset,
            "volume": 100.5 + offset,
            "quote_volume": 4_271_250.0 + offset,
            "trades": 12_345 + offset,
            "source": "fixture",
        }
        for offset in range(3)
    ]


def _frame(*, ingested_at_ms: int = INGESTED_AT_MS, shuffle: bool = False) -> pl.DataFrame:
    rows = _rows()
    if shuffle:
        rows = list(reversed(rows))
    return bar_frame(rows, ingested_at_ms=ingested_at_ms)


def test_canonical_hash_is_row_order_invariant() -> None:
    assert content_sha256(_frame()) == content_sha256(_frame(shuffle=True))


def test_canonical_hash_ignores_ingested_at() -> None:
    # ingested_at is bookkeeping; including it would break AC1 re-ingest identity.
    first = content_sha256(_frame(ingested_at_ms=1))
    second = content_sha256(_frame(ingested_at_ms=2))
    assert first == second
    assert b"ingested_at" not in canonical_bytes(_frame())


def test_canonical_bytes_are_one_framable_line_per_row() -> None:
    raw = canonical_bytes(_frame())
    lines = raw.split(b"\n")
    assert len(lines) == 3
    assert not raw.endswith(b"\n")
    assert all(b"\r" not in line and b"\t" not in line for line in lines)


def test_canonical_bytes_exclude_fields_outside_the_spec_identity() -> None:
    row = json.loads(canonical_bytes(_frame()).split(b"\n")[0])
    assert sorted(row) == sorted(CANONICAL_COLUMNS)
    assert "ingested_at" not in row


def test_content_hash_matches_the_frozen_golden() -> None:
    # Section 14.1 golden fixture: changing this literal needs a PR note.
    assert content_sha256(_frame()) == GOLDEN_CONTENT_SHA256


def test_partition_path_shape_is_fixed() -> None:
    rel = part_rel_path(
        layer=Layer.NORMALIZED,
        venue="BINANCE",
        market_type="SPOT",
        native_symbol="BTCUSDT",
        timeframe="1D",
        year=2024,
        sha256=GOLDEN_CONTENT_SHA256,
    )
    assert rel == (
        "normalized/venue=BINANCE/market_type=SPOT/symbol=BTCUSDT/tf=1D/year=2024/"
        f"part-{GOLDEN_CONTENT_SHA256[:16]}.parquet"
    )


def test_path_components_cannot_traverse_out_of_the_data_root() -> None:
    with pytest.raises(ValueError, match="unsafe native_symbol"):
        part_rel_path(
            layer=Layer.NORMALIZED,
            venue="BINANCE",
            market_type="SPOT",
            native_symbol="../etc",
            timeframe="1D",
            year=2024,
            sha256="a" * 64,
        )


def test_write_is_content_addressed_atomic_and_read_only(tmp_path: Path) -> None:
    ref = write_part(
        _frame(),
        data_dir=tmp_path,
        venue="BINANCE",
        market_type="SPOT",
        native_symbol="BTCUSDT",
        timeframe="1D",
    )
    assert ref.path == part_rel_path(
        layer=Layer.NORMALIZED,
        venue="BINANCE",
        market_type="SPOT",
        native_symbol="BTCUSDT",
        timeframe="1D",
        year=2024,
        sha256=content_sha256(_frame()),
    )
    assert ref.rows == 3
    assert ref.content_sha256 == GOLDEN_CONTENT_SHA256
    assert part_exists(tmp_path, ref.path)

    target = tmp_path.joinpath(*ref.path.split("/"))
    assert not target.with_name(target.name + ".tmp").exists()
    attributes = getattr(target.stat(), "st_file_attributes", None)
    if attributes is not None:  # Windows: the read-only attribute must be set.
        assert attributes & stat.FILE_ATTRIBUTE_READONLY
    with pytest.raises(PermissionError), open(target, "ab"):
        pass


def test_overwriting_an_existing_part_is_refused(tmp_path: Path) -> None:
    kwargs = {
        "data_dir": tmp_path,
        "venue": "BINANCE",
        "market_type": "SPOT",
        "native_symbol": "BTCUSDT",
        "timeframe": "1D",
    }
    write_part(_frame(), **kwargs)  # type: ignore[arg-type]
    with pytest.raises(ImmutableResultError) as exc:
        write_part(_frame(), **kwargs)  # type: ignore[arg-type]
    assert exc.value.code == "IMMUTABLE_RESULT"
    assert exc.value.detail["path"].startswith("normalized/venue=BINANCE/")


def test_written_frame_round_trips_sorted_with_ingested_at(tmp_path: Path) -> None:
    source = _frame(shuffle=True)
    ref = write_part(
        source,
        data_dir=tmp_path,
        venue="BINANCE",
        market_type="SPOT",
        native_symbol="BTCUSDT",
        timeframe="1D",
    )
    back = read_part(tmp_path, ref.path)
    assert back.columns == list(BAR_COLUMNS)
    assert back.equals(source.sort("open_time", maintain_order=True))


def test_write_refuses_duplicate_open_times(tmp_path: Path) -> None:
    rows = _rows()
    rows.append(dict(rows[-1]))
    with pytest.raises(ValueError, match="duplicate open_time"):
        write_part(
            bar_frame(rows, ingested_at_ms=INGESTED_AT_MS),
            data_dir=tmp_path,
            venue="BINANCE",
            market_type="SPOT",
            native_symbol="BTCUSDT",
            timeframe="1D",
        )


def test_write_refuses_mixed_partition_years(tmp_path: Path) -> None:
    rows = _rows()
    rows[-1] = {**rows[-1], "open_time": rows[-1]["open_time"] + 366 * DAY_MS,
                "close_time": rows[-1]["close_time"] + 366 * DAY_MS}
    with pytest.raises(ValueError, match="multiple partition years"):
        write_part(
            bar_frame(rows, ingested_at_ms=INGESTED_AT_MS),
            data_dir=tmp_path,
            venue="BINANCE",
            market_type="SPOT",
            native_symbol="BTCUSDT",
            timeframe="1D",
        )


def test_write_refuses_non_finite_values(tmp_path: Path) -> None:
    rows = _rows()
    rows[0] = {**rows[0], "close": float("nan")}
    with pytest.raises(ValueError, match="non-finite"):
        write_part(
            bar_frame(rows, ingested_at_ms=INGESTED_AT_MS),
            data_dir=tmp_path,
            venue="BINANCE",
            market_type="SPOT",
            native_symbol="BTCUSDT",
            timeframe="1D",
        )


def test_write_refuses_an_empty_frame(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        write_part(
            bar_frame([], ingested_at_ms=INGESTED_AT_MS),
            data_dir=tmp_path,
            venue="BINANCE",
            market_type="SPOT",
            native_symbol="BTCUSDT",
            timeframe="1D",
        )


def test_list_parts_finds_parts_across_years(tmp_path: Path) -> None:
    write_part(
        _frame(),
        data_dir=tmp_path,
        venue="BINANCE",
        market_type="SPOT",
        native_symbol="BTCUSDT",
        timeframe="1D",
    )
    second_rows = [
        {
            **row,
            "open_time": row["open_time"] + 366 * DAY_MS,
            "close_time": row["close_time"] + 366 * DAY_MS,
        }
        for row in _rows()
    ]
    write_part(
        bar_frame(second_rows, ingested_at_ms=INGESTED_AT_MS),
        data_dir=tmp_path,
        venue="BINANCE",
        market_type="SPOT",
        native_symbol="BTCUSDT",
        timeframe="1D",
    )
    found = list_parts(
        tmp_path,
        venue="BINANCE",
        market_type="SPOT",
        native_symbol="BTCUSDT",
        timeframe="1D",
    )
    assert len(found) == 2
    assert all(path.endswith(".parquet") and "part-" in path for path in found)
    assert list_parts(
        tmp_path,
        venue="BINANCE",
        market_type="SPOT",
        native_symbol="ETHUSDT",
        timeframe="1D",
    ) == []


def test_part_exists_is_false_for_an_unknown_path(tmp_path: Path) -> None:
    assert part_exists(tmp_path, "normalized/venue=BINANCE/part-deadbeef.parquet") is False


def test_extra_columns_change_the_content_hash() -> None:
    # The derived incomplete flag is identity: a flagged week is different content
    # and must never collide with the unflagged one on one content-addressed path.
    base = _frame()
    flagged = base.with_columns(pl.lit(True).alias("incomplete"))
    unflagged = base.with_columns(pl.lit(False).alias("incomplete"))
    sha_flagged = content_sha256(flagged, extra_columns=("incomplete",))
    sha_unflagged = content_sha256(unflagged, extra_columns=("incomplete",))
    assert sha_flagged != sha_unflagged
    assert sha_unflagged != content_sha256(base)


def test_extra_columns_are_stored_and_round_trip(tmp_path: Path) -> None:
    frame = _frame().with_columns(pl.lit(False).alias("incomplete"))
    ref = write_part(
        frame,
        data_dir=tmp_path,
        venue="BINANCE",
        market_type="SPOT",
        native_symbol="BTCUSDT",
        timeframe="1W",
        layer=Layer.DERIVED,
        extra_columns=("incomplete",),
    )
    back = read_part(tmp_path, ref.path)
    assert back.columns == [*BAR_COLUMNS, "incomplete"]
    assert back["incomplete"].to_list() == [False, False, False]
    assert ref.path.startswith("derived/venue=BINANCE/")
    assert ref.content_sha256 == content_sha256(frame, extra_columns=("incomplete",))


def test_extra_columns_may_not_redefine_identity() -> None:
    with pytest.raises(ValueError, match="may not name canonical columns"):
        content_sha256(_frame(), extra_columns=("ingested_at",))
    with pytest.raises(ValueError, match="may not name canonical columns"):
        content_sha256(_frame(), extra_columns=("open",))


def test_missing_extra_columns_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing canonical columns"):
        content_sha256(_frame(), extra_columns=("incomplete",))
    with pytest.raises(ValueError, match="missing bar columns"):
        write_part(
            _frame(),
            data_dir=tmp_path,
            venue="BINANCE",
            market_type="SPOT",
            native_symbol="BTCUSDT",
            timeframe="1W",
            layer=Layer.DERIVED,
            extra_columns=("incomplete",),
        )


def test_unexpected_columns_are_refused_instead_of_dropped(tmp_path: Path) -> None:
    frame = _frame().with_columns(pl.lit(1).alias("junk"))
    with pytest.raises(ValueError, match="unexpected columns"):
        write_part(
            frame,
            data_dir=tmp_path,
            venue="BINANCE",
            market_type="SPOT",
            native_symbol="BTCUSDT",
            timeframe="1D",
        )
