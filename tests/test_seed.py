"""Committed fixtures and the seed path (design note section 9, T1.4).

Acceptance evidence produced here:
- the recorded golden files are valid, strictly ordered and cover the
  owner-approved window from 2024-01-01 (section 14.1: re-recording one
  needs a PR note -- the counts below may only grow);
- ``run_seed`` writes through ``run_ingest`` (same writer, manifest and
  Guardian path as live ingest) and is deterministic: same fixtures -> the
  same ``dataset_version``, and the second run refetches nothing (the offline
  analogue of AC1);
- a missing fixture root fails closed with ``DATA_UNAVAILABLE`` instead of
  falling back to a live fetch (PD-10).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from apps.api.settings import get_settings
from engine.data.bars import BAR_MS, Timeframe
from engine.data.seed import (
    FIXTURES_ROOT,
    SEED_NATIVE_SYMBOLS,
    SEED_TIMEFRAMES,
    FixtureKlineAdapter,
    fixture_path,
    load_fixture,
    run_seed,
    write_fixture,
)
from engine.errors import DataUnavailableError, ValidationError
from tests.support import DAY, INGEST_AT, NOW, T0

#: Recorded 2026-10-08 from 2024-01-01: 1011 days, exactly contiguous.
_MIN_BARS = {Timeframe.D1: 1011, Timeframe.H4: 6066, Timeframe.H1: 24264}


@pytest.mark.parametrize("timeframe", [Timeframe.D1, Timeframe.H4, Timeframe.H1])
def test_committed_fixture_is_valid_golden_data(timeframe: Timeframe) -> None:
    for symbol in SEED_NATIVE_SYMBOLS:
        fixture = load_fixture(FIXTURES_ROOT, symbol, timeframe)
        assert fixture.range_start_ms == T0  # the owner-approved 2024-01-01 window
        assert len(fixture.klines) >= _MIN_BARS[timeframe]
        assert fixture.klines[0].open_time == fixture.range_start_ms
        assert fixture.klines[-1].open_time == fixture.range_end_ms
        for kline in fixture.klines:
            assert kline.low <= min(kline.open, kline.close)
            assert kline.high >= max(kline.open, kline.close)
            assert kline.volume >= 0.0
            assert kline.trades >= 0


def test_malformed_fixture_envelope_is_rejected(tmp_path: Path) -> None:
    path = fixture_path(tmp_path, "BTCUSDT", Timeframe.D1)
    path.parent.mkdir(parents=True)
    path.write_text('{"rows": []}', encoding="utf-8")
    with pytest.raises(ValidationError, match="malformed"):
        load_fixture(tmp_path, "BTCUSDT", Timeframe.D1)


def test_missing_fixture_file_is_data_unavailable(tmp_path: Path) -> None:
    with pytest.raises(DataUnavailableError):
        load_fixture(tmp_path, "BTCUSDT", Timeframe.D1)


# --------------------------------------------------------------------------
# The seed path itself (committed-style reads only; the transaction rolls back)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def tx(engine: Engine) -> Iterator[Connection]:
    """Clean slate for the tables seed writes, all inside one rolled-back tx.

    ``run_ingest`` reads committed gaps during the weekly derivation, so the
    leftovers of earlier modules are removed first -- and rolled back with
    the rest, leaving the database as it was.
    """
    connection = engine.connect()
    connection.begin()
    connection.execute(text("DELETE FROM system.gap"))
    connection.execute(text("DELETE FROM system.data_quality"))
    connection.execute(text("DELETE FROM market.dataset_manifest"))
    connection.execute(text("DELETE FROM system.audit_log"))
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "data"
    directory.mkdir(parents=True)
    return directory


def _tiny_rows(timeframe: Timeframe) -> list[list[object]]:
    """Contiguous bars over the golden window: complete for every timeframe."""
    bar_ms = BAR_MS[timeframe]
    count = (9 * DAY) // bar_ms + 1
    return [
        [T0 + index * bar_ms, "100.0", "105.0", "95.0", "101.0", "50.0", "5050.0", 10]
        for index in range(count)
    ]


def _write_tiny_root(root: Path) -> Path:
    for symbol in SEED_NATIVE_SYMBOLS:
        for timeframe in SEED_TIMEFRAMES:
            write_fixture(
                fixture_path(root, symbol, timeframe),
                source="test://fixture",
                request={"symbol": symbol, "interval": timeframe.value},
                recorded_at_ms=INGEST_AT,
                rows=_tiny_rows(timeframe),
            )
    return root


async def test_seed_without_a_recorded_root_fails_closed(tx: Connection, data_dir: Path) -> None:
    with pytest.raises(DataUnavailableError):
        await run_seed(
            tx,
            data_dir=data_dir,
            now_ms=NOW,
            ingested_at_ms=INGEST_AT,
            fixture_root=Path(__file__).parent / "no-such-fixtures",
        )


async def test_seed_is_deterministic_and_never_refetches(
    tx: Connection, data_dir: Path, tmp_path: Path
) -> None:
    root = _write_tiny_root(tmp_path / "fixtures")
    first_adapter = FixtureKlineAdapter(root)
    second_adapter = FixtureKlineAdapter(root)

    first = await run_seed(
        tx,
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
        fixture_root=root,
        adapter=first_adapter,
    )
    second = await run_seed(
        tx,
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
        fixture_root=root,
        adapter=second_adapter,
    )

    # Same fixtures, same params -> identical identity (PD-6 reproducibility).
    assert first.dataset_version == second.dataset_version
    assert len(first.dataset_version) == 64
    # Native bars written once, reused on the re-run; 1D derives 1W both times.
    assert first.files_written == 9 and first.files_reused == 0
    assert second.files_written == 0 and second.files_reused == 9
    assert first.derived_written >= 1
    assert first_adapter.fetch_calls != []
    assert second_adapter.fetch_calls == []
