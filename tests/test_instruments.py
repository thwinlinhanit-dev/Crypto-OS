"""Spec section 4.1 / 10: instrument identity and the registry."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from apps.api.settings import get_settings
from engine.data.instruments import (
    Instrument,
    MarketType,
    build_instrument,
    instrument_id_for,
    list_instruments,
    upsert_instruments,
)


def _instrument(**overrides: object) -> Instrument:
    payload: dict[str, object] = {
        "venue": "BINANCE",
        "market_type": MarketType.SPOT,
        "asset": "BTC",
        "quote": "USDT",
        "native_symbol": "BTCUSDT",
        "tick_size": "0.01000000",
        "lot_step": "0.00001000",
    }
    payload.update(overrides)
    return build_instrument(**payload)  # type: ignore[arg-type]


def test_instrument_id_is_deterministic_and_identity_scoped() -> None:
    # Deterministic so re-seeding never duplicates an instrument (PD-6 reproducibility).
    first = instrument_id_for("BINANCE", MarketType.SPOT, "BTCUSDT")
    second = instrument_id_for("BINANCE", MarketType.SPOT, "BTCUSDT")
    assert first == second
    assert isinstance(first, uuid.UUID)
    assert instrument_id_for("BINANCE", MarketType.SPOT, "ETHUSDT") != first
    assert instrument_id_for("BINANCE", MarketType.PERPETUAL, "BTCUSDT") != first
    assert instrument_id_for("BYBIT", MarketType.SPOT, "BTCUSDT") != first


def test_identity_tuple_and_display_ref() -> None:
    instrument = _instrument()
    assert instrument.instrument_id == instrument_id_for("BINANCE", MarketType.SPOT, "BTCUSDT")
    assert (instrument.venue, instrument.market_type, instrument.asset, instrument.quote) == (
        "BINANCE",
        MarketType.SPOT,
        "BTC",
        "USDT",
    )
    # section 4.1 display form used by the evidence payload ("BTC/USDT spot").
    assert instrument.ref == "BTC/USDT spot"


def test_market_type_vocabulary_is_spot_or_perpetual() -> None:
    assert {m.value for m in MarketType} == {"SPOT", "PERPETUAL"}
    assert MarketType.SPOT.value == "SPOT"
    # The data plane refuses PERPETUAL until M10 (section 4.8); identity does not.
    assert _instrument(market_type=MarketType.PERPETUAL).market_type is MarketType.PERPETUAL


def test_tick_and_lot_metadata_survive_the_round_trip() -> None:
    instrument = _instrument()
    assert instrument.tick_size is not None and str(instrument.tick_size) == "0.01000000"
    assert instrument.lot_step is not None and str(instrument.lot_step) == "0.00001000"
    assert _instrument(tick_size=None).tick_size is None


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def tx(engine: Engine) -> Iterator[Connection]:
    """A transaction every test rolls back: the suite leaves no registry rows.

    Committed rows are cleared first (``make seed-fixtures`` writes BTC/ETH/SOL
    for real), so every test starts from an empty registry; the rollback puts
    the committed state back untouched. gap/data_quality go before the
    registry -- they hold the FK to market.instrument.
    """
    conn = engine.connect()
    conn.begin()
    conn.execute(text("DELETE FROM system.gap"))
    conn.execute(text("DELETE FROM system.data_quality"))
    conn.execute(text("DELETE FROM market.instrument"))
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_upsert_is_idempotent(tx: Connection) -> None:
    btc = _instrument()
    eth = _instrument(asset="ETH", native_symbol="ETHUSDT")
    upsert_instruments(tx, [btc, eth])
    upsert_instruments(tx, [btc, eth])  # re-seed must not duplicate (AC: re-ingest)

    rows = tx.execute(
        text(
            "SELECT instrument_id, asset FROM market.instrument "
            "WHERE venue = 'BINANCE' AND market_type = 'SPOT' ORDER BY asset"
        )
    ).all()
    assert [(r.instrument_id, r.asset) for r in rows] == [
        (btc.instrument_id, "BTC"),
        (eth.instrument_id, "ETH"),
    ]


def test_upsert_refreshes_metadata_without_changing_the_id(tx: Connection) -> None:
    stale = _instrument(tick_size="1")
    upsert_instruments(tx, [stale])
    fresh = _instrument(tick_size="0.00010000", lot_step="0.00100000")
    upsert_instruments(tx, [fresh])

    row = tx.execute(
        text(
            "SELECT instrument_id, tick_size, lot_step FROM market.instrument "
            "WHERE native_symbol = 'BTCUSDT'"
        )
    ).one()
    assert row.instrument_id == fresh.instrument_id
    # numeric(30,18) round-trips the value; compare numerically, not by string.
    assert row.tick_size == Decimal("0.00010000")
    assert row.lot_step == Decimal("0.00100000")


def test_registry_uniqueness_is_enforced_by_the_database(tx: Connection) -> None:
    upsert_instruments(tx, [_instrument()])
    with pytest.raises(IntegrityError):
        tx.execute(
            text(
                "INSERT INTO market.instrument "
                "(instrument_id, venue, market_type, asset, quote, native_symbol) "
                "VALUES (:id, 'BINANCE', 'SPOT', 'BTC', 'USDT', 'BTCUSDT')"
            ),
            {"id": uuid.uuid4()},
        )
    tx.rollback()
    tx.begin()


def test_market_type_check_rejects_anything_but_spot_or_perpetual(tx: Connection) -> None:
    with pytest.raises(IntegrityError):
        tx.execute(
            text(
                "INSERT INTO market.instrument "
                "(instrument_id, venue, market_type, asset, quote, native_symbol) "
                "VALUES (:id, 'BINANCE', 'FUTURES', 'BTC', 'USDT', 'BTCUSDT_F')"
            ),
            {"id": uuid.uuid4()},
        )
    tx.rollback()
    tx.begin()


def test_list_instruments_filters_by_the_identity_fields(tx: Connection) -> None:
    upsert_instruments(
        tx,
        [
            _instrument(),
            _instrument(asset="ETH", native_symbol="ETHUSDT"),
            _instrument(
                market_type=MarketType.PERPETUAL,
                native_symbol="BTCUSDT",
                asset="BTC",
            ),
            _instrument(venue="BYBIT", native_symbol="BTCUSDT"),
        ],
    )
    all_rows = list_instruments(tx)
    assert len(all_rows) == 4
    # BTC spot + BTC perpetual + BYBIT BTC spot
    assert len(list_instruments(tx, asset="BTC")) == 3
    perp = list_instruments(tx, market_type=MarketType.PERPETUAL)
    assert len(perp) == 1 and perp[0].market_type is MarketType.PERPETUAL
    assert len(list_instruments(tx, venue="BYBIT")) == 1
    assert len(list_instruments(tx, venue="BYBIT", market_type=MarketType.SPOT)) == 1
    assert list_instruments(tx, venue="BYBIT", market_type=MarketType.PERPETUAL) == []


def test_gap_rows_must_reference_a_registered_instrument(tx: Connection) -> None:
    instrument = _instrument()
    upsert_instruments(tx, [instrument])
    tx.execute(
        text(
            "INSERT INTO system.gap (gap_id, instrument_id, timeframe, exclusion_start, "
            "exclusion_end, status, source, detected_at) VALUES "
            "(:gap, :inst, '1D', now(), now(), 'OPEN', 'guardian', now())"
        ),
        {"gap": uuid.uuid4(), "inst": instrument.instrument_id},
    )
    with pytest.raises(IntegrityError):
        tx.execute(
            text(
                "INSERT INTO system.gap (gap_id, instrument_id, timeframe, exclusion_start, "
                "exclusion_end, status, source, detected_at) VALUES "
                "(:gap, :inst, '1D', now(), now(), 'OPEN', 'guardian', now())"
            ),
            {"gap": uuid.uuid4(), "inst": uuid.uuid4()},
        )
    tx.rollback()
    tx.begin()
