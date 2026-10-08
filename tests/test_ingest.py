"""Spec section 4.5/4.6 + T1.2: the ingest pipeline.

Acceptance evidence produced here:
- AC1  re-ingest of an identical scope yields the identical ``dataset_version``
       and never contacts the venue again (PD-6 / section 4.4);
- AC2  the manifest answers "what exactly did this use?" (section 4.4);
- AC3  an injected gap is detected with its exact exclusion interval, and the
       repair flow round-trips it to REPAIRED with a new dataset, or to
       UNREPAIRABLE while staying excluded (section 4.6);
- AC4  a perpetual scope fails closed with NOT_IMPLEMENTED (section 4.8);
- AC5  the storage budget refuses before anything is written (OD-5).

The spec is silent on two behaviours this file pins down and records as
INTERPRETATION (design note section 5, DECISIONS): a repaired (REPAIRED) or
already-UNREPAIRABLE gap id may be retried, while any other status is refused;
and a scope that mixes instruments from different venues/markets is incoherent
and refused (PD-12).  ``run_resample`` derives from stored 1D parts and treats
their absence as DATA_UNAVAILABLE (design note section 7).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from apps.api.settings import get_settings
from engine.data.bars import Timeframe
from engine.data.guardian import (
    QualityState,
    get_gap,
    latest_assessments,
    load_exclusions,
)
from engine.data.ingest import (
    IngestScope,
    run_ingest,
    run_repair,
    run_resample,
)
from engine.data.instruments import (
    MarketType,
    build_instrument,
    upsert_instruments,
)
from engine.data.manifest import get_manifest
from engine.data.parquet import Layer, list_parts, read_part
from engine.errors import (
    DataUnavailableError,
    NotFoundError,
    NotImplementedMarketError,
    ValidationError,
)
from tests.support import (
    BTC,
    DAY,
    INGEST_AT,
    NOW,
    T0,
    WIDE_BUDGET,
    FixtureAdapter,
    daily,
    kline,
)


def _scope(**overrides: Any) -> IngestScope:
    base: dict[str, Any] = {
        "venue": "BINANCE",
        "market_type": MarketType.SPOT,
        "instrument_ids": (BTC.instrument_id,),
        "timeframes": (Timeframe.D1,),
        "start_ms": T0,
        "end_ms": T0 + 9 * DAY,
        "storage_budget_bytes": WIDE_BUDGET,
    }
    base.update(overrides)
    return IngestScope(**base)


def _parts(
    data_dir: Path, timeframe: Timeframe = Timeframe.D1, layer: Layer = Layer.NORMALIZED
) -> list[str]:
    return list_parts(
        data_dir,
        venue=BTC.venue,
        market_type=BTC.market_type.value,
        native_symbol=BTC.native_symbol,
        timeframe=timeframe,
        layer=layer,
    )


def _files(data_dir: Path) -> list[str]:
    return sorted(
        str(path.relative_to(data_dir)).replace("\\", "/") for path in data_dir.rglob("*.parquet")
    )


def _write_n_bytes(data_dir: Path, n: int) -> None:
    """Occupied disk with non-parquet bytes (the budget checks every byte)."""
    blob = data_dir / "filler.bin"
    blob.write_bytes(b"\x00" * n)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def tx(engine: Engine) -> Iterator[Connection]:
    conn = engine.connect()
    conn.begin()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def conn(tx: Connection) -> Connection:
    upsert_instruments(tx, [BTC])
    return tx


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "data"
    directory.mkdir(parents=True)
    return directory


# --------------------------------------------------------------------------
# AC1 + AC2: happy path, idempotent re-ingest
# --------------------------------------------------------------------------


async def test_ingest_writes_parts_manifest_and_green_assessment(
    conn: Connection, data_dir: Path
) -> None:
    adapter = FixtureAdapter({("BTCUSDT", Timeframe.D1): daily()})
    result = await run_ingest(
        conn,
        _scope(),
        adapter,
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )

    assert len(result.dataset_version) == 64
    assert result.files_written == 1 and result.files_reused == 0
    parts = _parts(data_dir)
    assert len(parts) == 1
    assert read_part(data_dir, parts[0]).height == 10

    manifest = get_manifest(conn, result.dataset_version)
    assert manifest is not None
    assert manifest.instruments == (str(BTC.instrument_id),)
    assert manifest.timeframes == ("1D",)
    assert manifest.venue == "BINANCE" and manifest.market_type == "SPOT"
    assert manifest.start_time == datetime.fromtimestamp(T0 / 1000, tz=UTC)
    assert manifest.end_time == datetime.fromtimestamp((T0 + 9 * DAY) / 1000, tz=UTC)
    # AC2: the manifest names exactly the bytes the result was built from.
    assert [entry.path for entry in manifest.source_files] == parts
    assert manifest.row_counts == {parts[0]: 10}
    assert (
        manifest.source_files[0].content_sha256
        and len(manifest.source_files[0].content_sha256) == 64
    )
    assert manifest.missing_ranges == []
    series = manifest.quality_summary["series"]
    assert series[0]["state"] == "GREEN" and series[0]["completeness"] == pytest.approx(1.0)

    assessment = latest_assessments(conn, instrument_id=BTC.instrument_id)
    assert len(assessment) == 1
    assert assessment[0].state is QualityState.GREEN
    assert assessment[0].completeness == pytest.approx(1.0)
    assert load_exclusions(conn, instrument_id=BTC.instrument_id) == []
    assert len(adapter.fetch_calls) == 1

    actions = conn.execute(text("SELECT action, target FROM system.audit_log ORDER BY id")).all()
    assert [(row.action, row.target) for row in actions] == [("ingest", result.dataset_version)]


async def test_reingest_is_idempotent_and_never_refetches(conn: Connection, data_dir: Path) -> None:
    bars = {("BTCUSDT", Timeframe.D1): daily()}
    first = await run_ingest(
        conn,
        _scope(),
        FixtureAdapter(bars),
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )
    files_after_first = _files(data_dir)

    second_adapter = FixtureAdapter(bars)
    second = await run_ingest(
        conn, _scope(), second_adapter, data_dir=data_dir, now_ms=NOW, ingested_at_ms=INGEST_AT
    )

    # AC1: identical scope -> identical identity, no new bytes, no venue call.
    assert second.dataset_version == first.dataset_version
    assert second_adapter.fetch_calls == []
    assert second.files_written == 0 and second.files_reused == 1
    assert _files(data_dir) == files_after_first


# --------------------------------------------------------------------------
# AC3: gap detection with the exact exclusion interval
# --------------------------------------------------------------------------


async def test_missing_day_opens_one_exact_exclusion_interval(
    conn: Connection, data_dir: Path
) -> None:
    adapter = FixtureAdapter({("BTCUSDT", Timeframe.D1): daily(skip=(4,))})
    result = await run_ingest(
        conn, _scope(), adapter, data_dir=data_dir, now_ms=NOW, ingested_at_ms=INGEST_AT
    )

    gaps = load_exclusions(conn, instrument_id=BTC.instrument_id, timeframe="1D")
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.kind == "gap" and gap.status == "OPEN"
    assert gap.exclusion_start == datetime.fromtimestamp((T0 + 4 * DAY) / 1000, tz=UTC)
    assert gap.exclusion_end == datetime.fromtimestamp((T0 + 5 * DAY) / 1000, tz=UTC)

    manifest = get_manifest(conn, result.dataset_version)
    assert manifest is not None
    assert len(manifest.missing_ranges) == 1
    entry = manifest.missing_ranges[0]
    assert entry["kind"] == "gap"
    assert entry["start_ms"] == T0 + 4 * DAY
    assert entry["end_ms"] == T0 + 5 * DAY

    assessment = latest_assessments(conn, instrument_id=BTC.instrument_id)[0]
    assert assessment.state is QualityState.ORANGE
    assert assessment.completeness == pytest.approx(0.9)

    # Only the affected interval is excluded; the other nine bars are stored.
    parts = _parts(data_dir)
    assert read_part(data_dir, parts[0]).height == 9


async def test_repair_fills_the_gap_marks_repaired_and_opens_a_new_dataset(
    conn: Connection, data_dir: Path
) -> None:
    missing = {("BTCUSDT", Timeframe.D1): daily(skip=(4,))}
    first = await run_ingest(
        conn,
        _scope(),
        FixtureAdapter(missing),
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )
    open_gaps = load_exclusions(conn, instrument_id=BTC.instrument_id, status="OPEN")
    assert len(open_gaps) == 1

    full = {("BTCUSDT", Timeframe.D1): daily()}
    repair = await run_repair(
        conn,
        gap_id=open_gaps[0].gap_id,
        adapter=FixtureAdapter(full),
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
        storage_budget_bytes=WIDE_BUDGET,
    )

    assert repair.status == "REPAIRED"
    assert repair.dataset_version is not None
    assert repair.dataset_version != first.dataset_version

    after = get_gap(conn, open_gaps[0].gap_id)
    assert after is not None and after.status == "REPAIRED"
    assert after.repaired_dataset_version == repair.dataset_version

    repaired_manifest = get_manifest(conn, repair.dataset_version)
    assert repaired_manifest is not None
    assert len(repaired_manifest.source_files) == 1
    assert sum(repaired_manifest.row_counts.values()) == 10

    # The repaired range stops excluding (section 4.6) and health turns GREEN.
    assert load_exclusions(conn, instrument_id=BTC.instrument_id, active_only=True) == []
    latest = latest_assessments(conn, instrument_id=BTC.instrument_id)[0]
    assert latest.state is QualityState.GREEN
    assert latest.completeness == pytest.approx(1.0)

    # A re-ingest now needs no fetch and reproduces the repaired identity.
    follow_adapter = FixtureAdapter(full)
    follow = await run_ingest(
        conn, _scope(), follow_adapter, data_dir=data_dir, now_ms=NOW, ingested_at_ms=INGEST_AT
    )
    assert follow_adapter.fetch_calls == []
    assert follow.dataset_version == repair.dataset_version


async def test_repair_with_still_missing_bar_marks_unrepairable_and_stays_excluded(
    conn: Connection, data_dir: Path
) -> None:
    missing = {("BTCUSDT", Timeframe.D1): daily(skip=(4,))}
    await run_ingest(
        conn,
        _scope(),
        FixtureAdapter(missing),
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )
    gap = load_exclusions(conn, status="OPEN")[0]
    files_before = _files(data_dir)

    repair = await run_repair(
        conn,
        gap_id=gap.gap_id,
        adapter=FixtureAdapter(missing),  # the venue still has no 2024-01-05 bar
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
        storage_budget_bytes=WIDE_BUDGET,
    )

    assert repair.status == "UNREPAIRABLE"
    assert repair.dataset_version is None
    after = get_gap(conn, gap.gap_id)
    assert after is not None and after.status == "UNREPAIRABLE"
    # Section 4.6: an unrefetchable gap "stays excluded".
    active = load_exclusions(conn, instrument_id=BTC.instrument_id, active_only=True)
    assert [row.gap_id for row in active] == [gap.gap_id]
    assert _files(data_dir) == files_before


async def test_repair_unknown_gap_id_is_not_found(conn: Connection, data_dir: Path) -> None:
    with pytest.raises(NotFoundError) as excinfo:
        await run_repair(
            conn,
            gap_id=uuid.uuid4(),
            adapter=FixtureAdapter(),
            data_dir=data_dir,
            now_ms=NOW,
            ingested_at_ms=INGEST_AT,
            storage_budget_bytes=WIDE_BUDGET,
        )
    assert excinfo.value.code == "NOT_FOUND"
    assert excinfo.value.http_status == 404


async def test_repair_of_a_resolved_gap_is_refused(conn: Connection, data_dir: Path) -> None:
    missing = {("BTCUSDT", Timeframe.D1): daily(skip=(4,))}
    await run_ingest(
        conn,
        _scope(),
        FixtureAdapter(missing),
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )
    gap = load_exclusions(conn, status="OPEN")[0]
    await run_repair(
        conn,
        gap_id=gap.gap_id,
        adapter=FixtureAdapter({("BTCUSDT", Timeframe.D1): daily()}),
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
        storage_budget_bytes=WIDE_BUDGET,
    )

    with pytest.raises(ValidationError) as excinfo:
        await run_repair(
            conn,
            gap_id=gap.gap_id,
            adapter=FixtureAdapter(),
            data_dir=data_dir,
            now_ms=NOW,
            ingested_at_ms=INGEST_AT,
            storage_budget_bytes=WIDE_BUDGET,
        )
    assert excinfo.value.detail["status"] == "REPAIRED"


# --------------------------------------------------------------------------
# AC4: fail closed on market type and incoherent scopes (PD-12)
# --------------------------------------------------------------------------


async def test_perpetual_scope_fails_closed_before_fetching(
    conn: Connection, data_dir: Path
) -> None:
    adapter = FixtureAdapter()
    with pytest.raises(NotImplementedMarketError) as excinfo:
        await run_ingest(
            conn,
            _scope(market_type=MarketType.PERPETUAL),
            adapter,
            data_dir=data_dir,
            now_ms=NOW,
            ingested_at_ms=INGEST_AT,
        )
    assert excinfo.value.code == "NOT_IMPLEMENTED"
    assert excinfo.value.http_status == 501
    assert adapter.fetch_calls == []
    assert _files(data_dir) == []


async def test_derived_weekly_timeframe_is_refused_by_ingest(
    conn: Connection, data_dir: Path
) -> None:
    adapter = FixtureAdapter()
    with pytest.raises(ValidationError) as excinfo:
        await run_ingest(
            conn,
            _scope(timeframes=(Timeframe.W1,)),
            adapter,
            data_dir=data_dir,
            now_ms=NOW,
            ingested_at_ms=INGEST_AT,
        )
    assert excinfo.value.code == "VALIDATION_ERROR"
    assert excinfo.value.http_status == 422
    assert adapter.fetch_calls == []


async def test_scope_mismatch_instrument_is_refused(conn: Connection, data_dir: Path) -> None:
    okx_eth = build_instrument(
        venue="OKX", market_type="SPOT", asset="ETH", quote="USDT", native_symbol="ETH-USDT"
    )
    upsert_instruments(conn, [okx_eth])
    adapter = FixtureAdapter()
    with pytest.raises(ValidationError) as excinfo:
        await run_ingest(
            conn,
            _scope(instrument_ids=(okx_eth.instrument_id,)),
            adapter,
            data_dir=data_dir,
            now_ms=NOW,
            ingested_at_ms=INGEST_AT,
        )
    assert excinfo.value.detail["instrument_id"] == str(okx_eth.instrument_id)
    assert adapter.fetch_calls == []


async def test_unknown_instrument_is_not_found(conn: Connection, data_dir: Path) -> None:
    with pytest.raises(NotFoundError) as excinfo:
        await run_ingest(
            conn,
            _scope(instrument_ids=(uuid.uuid4(),)),
            FixtureAdapter(),
            data_dir=data_dir,
            now_ms=NOW,
            ingested_at_ms=INGEST_AT,
        )
    assert excinfo.value.http_status == 404


# --------------------------------------------------------------------------
# AC5: storage budget refuses before anything is written (OD-5)
# --------------------------------------------------------------------------


async def test_tiny_storage_budget_refuses_before_writing_anything(
    conn: Connection, data_dir: Path
) -> None:
    adapter = FixtureAdapter({("BTCUSDT", Timeframe.D1): daily()})
    with pytest.raises(ValidationError) as excinfo:
        await run_ingest(
            conn,
            _scope(storage_budget_bytes=10),
            adapter,
            data_dir=data_dir,
            now_ms=NOW,
            ingested_at_ms=INGEST_AT,
        )
    detail = excinfo.value.detail
    assert detail["reason"] == "storage_budget_exceeded"
    assert "used_gb" in detail and "budget_gb" in detail
    assert detail["budget_gb"] < 0.000001
    # Nothing reached disk and no manifest row was registered.
    assert _files(data_dir) == []
    count = conn.execute(text("SELECT count(*) FROM market.dataset_manifest")).scalar_one()
    assert count == 0


async def test_budget_below_existing_usage_refuses_before_fetching(
    conn: Connection, data_dir: Path
) -> None:
    _write_n_bytes(data_dir, 4096)
    adapter = FixtureAdapter()
    with pytest.raises(ValidationError) as excinfo:
        await run_ingest(
            conn,
            _scope(storage_budget_bytes=2048),
            adapter,
            data_dir=data_dir,
            now_ms=NOW,
            ingested_at_ms=INGEST_AT,
        )
    assert excinfo.value.detail["reason"] == "storage_budget_exceeded"
    assert adapter.fetch_calls == []


# --------------------------------------------------------------------------
# Section 4.3: derived weekly bars written by ingest (forming week excluded)
# --------------------------------------------------------------------------


async def test_ingest_derives_only_closed_complete_weekly_bars(
    conn: Connection, data_dir: Path
) -> None:
    bars = daily()
    result = await run_ingest(
        conn,
        _scope(),
        FixtureAdapter({("BTCUSDT", Timeframe.D1): bars}),
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )

    weekly_parts = _parts(data_dir, timeframe=Timeframe.W1, layer=Layer.DERIVED)
    assert len(weekly_parts) == 1
    weekly = read_part(data_dir, weekly_parts[0])
    assert "incomplete" in weekly.columns
    # Only the Jan 1-7 week is complete and closed; the Jan 8 week is forming.
    assert weekly.height == 1
    row = weekly.row(0, named=True)
    assert row["open_time"] == T0
    assert row["close_time"] == T0 + 7 * DAY
    assert row["incomplete"] is False
    assert row["volume"] == pytest.approx(sum(k.volume for k in bars[:7]))
    assert row["quote_volume"] == pytest.approx(sum(k.quote_volume for k in bars[:7]))
    assert result.derived_written == 1

    # The derived part is content-addressed under data/derived, never under
    # the normalized layer a dataset manifest references.
    assert all(part.startswith("derived/") for part in weekly_parts)


# --------------------------------------------------------------------------
# Section 4.6: corrupt payload rows are dropped, recorded and reported RED
# --------------------------------------------------------------------------


async def test_bad_rows_are_dropped_recorded_and_reported_red(
    conn: Connection, data_dir: Path
) -> None:
    bars = daily()
    payload = [*bars, kline(T0 + 6 * DAY, low_bad=True), bars[3]]  # OHLC violation, duplicate
    adapter = FixtureAdapter({("BTCUSDT", Timeframe.D1): payload})
    result = await run_ingest(
        conn, _scope(), adapter, data_dir=data_dir, now_ms=NOW, ingested_at_ms=INGEST_AT
    )

    assessment = latest_assessments(conn, instrument_id=BTC.instrument_id)[0]
    assert assessment.state is QualityState.RED
    causes = set(assessment.detail["causes"])
    assert {"ohlc_violations", "duplicate_bars"} <= causes

    # Only clean, unique rows were written: the bad day is dropped, the
    # duplicate collapses to its first arrival.
    parts = _parts(data_dir)
    frame = read_part(data_dir, parts[0])
    assert frame.height == 10
    assert frame["open_time"].to_list() == sorted(frame["open_time"].to_list())
    valid = frame.filter(
        (frame["low"] <= frame["open"])
        & (frame["close"] <= frame["high"])
        & (frame["low"] <= frame["high"])
    )
    assert valid.height == frame.height

    # The invalid ranges stay excluded and the manifest reports them.
    invalid = [
        row
        for row in load_exclusions(conn, instrument_id=BTC.instrument_id)
        if row.kind == "invalid"
    ]
    assert len(invalid) == 2
    assert {row.exclusion_start for row in invalid} == {
        datetime.fromtimestamp((T0 + 3 * DAY) / 1000, tz=UTC),
        datetime.fromtimestamp((T0 + 6 * DAY) / 1000, tz=UTC),
    }
    assert all(row.status == "OPEN" for row in invalid)
    manifest = get_manifest(conn, result.dataset_version)
    assert manifest is not None
    assert {entry["kind"] for entry in manifest.missing_ranges} == {"invalid"}


# --------------------------------------------------------------------------
# Section 4.3 / design note 7: standalone RESAMPLE job
# --------------------------------------------------------------------------


async def test_standalone_resample_requires_stored_daily_data(
    conn: Connection, data_dir: Path
) -> None:
    with pytest.raises(DataUnavailableError) as excinfo:
        run_resample(
            conn,
            _scope(timeframes=(Timeframe.W1,)),
            data_dir=data_dir,
            now_ms=NOW,
            ingested_at_ms=INGEST_AT,
        )
    assert excinfo.value.code == "DATA_UNAVAILABLE"
    assert excinfo.value.http_status == 400


async def test_standalone_resample_derives_and_registers_a_weekly_dataset(
    conn: Connection, data_dir: Path
) -> None:
    await run_ingest(
        conn,
        _scope(),
        FixtureAdapter({("BTCUSDT", Timeframe.D1): daily()}),
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )
    result = run_resample(
        conn,
        _scope(timeframes=(Timeframe.W1,)),
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )

    assert result.manifest.timeframes == ("1W",)
    assert sum(result.manifest.row_counts.values()) == 1
    weekly_parts = _parts(data_dir, timeframe=Timeframe.W1, layer=Layer.DERIVED)
    assert len(weekly_parts) == 1
    stored = get_manifest(conn, result.dataset_version)
    assert stored is not None and stored.timeframes == ("1W",)

    actions = [
        row.action
        for row in conn.execute(text("SELECT action FROM system.audit_log ORDER BY id")).all()
    ]
    assert actions == ["ingest", "resample"]
