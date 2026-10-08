"""Spec section 4.1 / 10: instrument identity and the registry (T1.1).

Identity is ``{asset, quote, market_type, venue, native_symbol}``. ``instrument_id``
is a deterministic uuid5 over that identity, so re-seeding a universe can never
duplicate an instrument and two machines derive the same id (PD-6).

All persistence is plain SQL against the ``market.instrument`` table created by
migration 0002 - the migration stays the single source of DDL truth.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from sqlalchemy import Connection, text


class MarketType(StrEnum):
    """``market.market_type`` values, supported from M0 (section 4.8)."""

    SPOT = "SPOT"
    PERPETUAL = "PERPETUAL"


#: Fixed namespace so ``instrument_id`` is reproducible on any machine.
INSTRUMENT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://cryptosos.local/instrument")

_SELECT_COLUMNS = (
    "instrument_id, venue, market_type, asset, quote, native_symbol, "
    "tick_size, lot_step, listed_at"
)


def instrument_id_for(venue: str, market_type: MarketType | str, native_symbol: str) -> uuid.UUID:
    key = f"{venue}|{MarketType(market_type).value}|{native_symbol}"
    return uuid.uuid5(INSTRUMENT_NAMESPACE, key)


def _decimal(value: Decimal | str | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(value)
    except InvalidOperation as cause:
        raise ValueError(f"invalid decimal value {value!r}") from cause


@dataclass(frozen=True, slots=True)
class Instrument:
    """Registry row. Research code holds ``instrument_id``, never a native symbol."""

    instrument_id: uuid.UUID
    venue: str
    market_type: MarketType
    asset: str
    quote: str
    native_symbol: str
    tick_size: Decimal | None = None
    lot_step: Decimal | None = None
    listed_at: datetime | None = None

    @property
    def ref(self) -> str:
        """Display form used in evidence payloads: ``BTC/USDT spot``."""
        return f"{self.asset}/{self.quote} {self.market_type.value.lower()}"


def build_instrument(
    *,
    venue: str,
    market_type: MarketType | str,
    asset: str,
    quote: str,
    native_symbol: str,
    tick_size: Decimal | str | None = None,
    lot_step: Decimal | str | None = None,
    listed_at: datetime | None = None,
) -> Instrument:
    kind = MarketType(market_type)
    return Instrument(
        instrument_id=instrument_id_for(venue, kind, native_symbol),
        venue=venue,
        market_type=kind,
        asset=asset,
        quote=quote,
        native_symbol=native_symbol,
        tick_size=_decimal(tick_size),
        lot_step=_decimal(lot_step),
        listed_at=listed_at,
    )


def upsert_instruments(conn: Connection, instruments: Iterable[Instrument]) -> None:
    """Insert or refresh registry rows; identity columns are never rewritten."""
    statement = text(
        f"INSERT INTO market.instrument ({_SELECT_COLUMNS}) "
        "VALUES (:instrument_id, :venue, :market_type, :asset, :quote, :native_symbol, "
        ":tick_size, :lot_step, :listed_at) "
        "ON CONFLICT (venue, market_type, native_symbol) DO UPDATE SET "
        "asset = EXCLUDED.asset, quote = EXCLUDED.quote, tick_size = EXCLUDED.tick_size, "
        "lot_step = EXCLUDED.lot_step, listed_at = EXCLUDED.listed_at"
    )
    for instrument in instruments:
        conn.execute(
            statement,
            {
                "instrument_id": instrument.instrument_id,
                "venue": instrument.venue,
                "market_type": instrument.market_type.value,
                "asset": instrument.asset,
                "quote": instrument.quote,
                "native_symbol": instrument.native_symbol,
                "tick_size": instrument.tick_size,
                "lot_step": instrument.lot_step,
                "listed_at": instrument.listed_at,
            },
        )


def list_instruments(
    conn: Connection,
    *,
    venue: str | None = None,
    market_type: MarketType | str | None = None,
    asset: str | None = None,
) -> list[Instrument]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if venue is not None:
        clauses.append("venue = :venue")
        params["venue"] = venue
    if market_type is not None:
        clauses.append("market_type = :market_type")
        params["market_type"] = MarketType(market_type).value
    if asset is not None:
        clauses.append("asset = :asset")
        params["asset"] = asset
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        text(f"SELECT {_SELECT_COLUMNS} FROM market.instrument{where} "
             "ORDER BY venue, market_type, asset, native_symbol"),
        params,
    ).all()
    return [_to_instrument(row) for row in rows]


def get_instrument(conn: Connection, instrument_id: uuid.UUID) -> Instrument | None:
    row = conn.execute(
        text(f"SELECT {_SELECT_COLUMNS} FROM market.instrument WHERE instrument_id = :id"),
        {"id": instrument_id},
    ).first()
    return None if row is None else _to_instrument(row)


def _to_instrument(row: Any) -> Instrument:
    return Instrument(
        instrument_id=row.instrument_id,
        venue=row.venue,
        market_type=MarketType(row.market_type),
        asset=row.asset,
        quote=row.quote,
        native_symbol=row.native_symbol,
        tick_size=row.tick_size,
        lot_step=row.lot_step,
        listed_at=row.listed_at,
    )
