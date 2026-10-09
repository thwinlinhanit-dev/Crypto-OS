"""Shared M1 test support: constants, bar builders and a fake venue adapter.

No test cases live here (pytest collects ``test_*`` modules only); ``test_ingest``,
``test_jobs`` and ``test_api_data`` import these helpers so the fake venue and the
golden time constants stay identical across modules.  ``FixtureAdapter`` serves
bars from memory and records every fetch, so tests can assert the AC1 "no refetch"
property without touching the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from engine.data.bars import Timeframe
from engine.data.instruments import MarketType, build_instrument
from exchanges.base import Kline

DAY = 86_400_000
GB = 1024**3
#: 2024-01-01T00:00:00Z (a Monday): ten daily bars, all closed by NOW.
T0 = 1_704_067_200_000
NOW = T0 + 11 * DAY  # 2024-01-12T00:00:00Z
INGEST_AT = NOW + 60_000

BTC = build_instrument(
    venue="BINANCE", market_type="SPOT", asset="BTC", quote="USDT", native_symbol="BTCUSDT"
)
WIDE_BUDGET = 100 * GB


def kline(open_ms: int, *, low_bad: bool = False) -> Kline:
    """One closed daily candle with distinct per-day prices (low > open when bad)."""
    day = (open_ms - T0) // DAY
    open_ = 100.0 + day
    return Kline(
        open_time=open_ms,
        close_time=open_ms + DAY,
        open=open_,
        high=open_ + 5.0,
        low=open_ + 1.0 if low_bad else open_ - 5.0,
        close=open_ + 1.0,
        volume=1000.0,
        quote_volume=open_ * 1000.0,
        trades=500,
    )


def daily(*, skip: Sequence[int] = ()) -> list[Kline]:
    """Ten daily bars over the golden window, minus the (zero-based) skipped days."""
    return [kline(T0 + i * DAY) for i in range(10) if i not in skip]


def hourly(count: int) -> list[Kline]:
    """``count`` contiguous hourly candles from the golden epoch, all closed."""
    rows: list[Kline] = []
    for index in range(count):
        open_ms = T0 + index * 3_600_000
        open_ = 100.0 + (index % 1000)
        rows.append(
            Kline(
                open_time=open_ms,
                close_time=open_ms + 3_600_000,
                open=open_,
                high=open_ + 5.0,
                low=open_ - 5.0,
                close=open_ + 1.0,
                volume=1000.0,
                quote_volume=open_ * 1000.0,
                trades=500,
            )
        )
    return rows


class FixtureAdapter:
    """Protocol-faithful fake venue: serves bars from memory, records fetches."""

    name = "fixture"
    market_type = MarketType.SPOT

    def __init__(self, bars: dict[tuple[str, Timeframe], list[Kline]] | None = None) -> None:
        self.bars = dict(bars or {})
        self.fetch_calls: list[tuple[str, Timeframe, int, int]] = []

    async def fetch_historical_klines(
        self, *, native_symbol: str, timeframe: Timeframe, start_ms: int, end_ms: int
    ) -> list[Kline]:
        self.fetch_calls.append((native_symbol, timeframe, start_ms, end_ms))
        rows = self.bars.get((native_symbol, timeframe), [])
        return [k for k in rows if start_ms <= k.open_time <= end_ms]

    async def fetch_instrument_metadata(
        self, *, native_symbols: Sequence[str]
    ) -> list[dict[str, Any]]:
        return []

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "server_time_ms": NOW}

    async def aclose(self) -> None:
        return None


#: Research tables, in one TRUNCATE: the M5b foreign keys (variant/experiment ->
#: family, experiment -> hypothesis) require the referencing tables to be
#: truncated together. Append-only tables use TRUNCATE because their row
#: triggers reject DELETE outright (PD-13).
RESEARCH_TABLES = (
    "research.event, research.event_outcome, research.stats_result,"
    " research.hypothesis, research.note, research.variant, research.experiment,"
    " research.experiment_family"
)


def reset_research(conn: Any, *, audit: bool = True) -> None:
    """Empty the research tables (and the audit log) for a clean test window."""
    from sqlalchemy import text

    if audit:
        conn.execute(text("DELETE FROM system.audit_log"))
    conn.execute(text(f"TRUNCATE {RESEARCH_TABLES}"))
    conn.commit()
