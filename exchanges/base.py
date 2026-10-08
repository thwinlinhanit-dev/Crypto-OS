"""Spec section 4.5 / Master section 6: the venue-agnostic adapter surface.

v1 exposes exactly the three candle methods the spec names; Master's live
subscription methods land with their milestone (M10, PD-9).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from engine.data.bars import Timeframe
from engine.data.instruments import MarketType

#: Injectable async sleep, so tests can observe rate-limit waits deterministically.
SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Kline:
    """One canonical candle after the venue-to-engine transformation (Master 6).

    ``close_time`` is the exclusive bar boundary (``open_time + bar_ms``,
    design note section 1 / D-008); the venue's own close value never leaves
    the adapter. ``instrument_id``, ``timeframe``, ``source`` and
    ``ingested_at`` are stamped by ingest, not here.
    """

    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades: int


class ExchangeAdapter(Protocol):
    """Candle-only adapter interface for v1 (section 4.5)."""

    name: str
    market_type: MarketType

    async def fetch_historical_klines(
        self,
        *,
        native_symbol: str,
        timeframe: Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> list[Kline]: ...

    async def fetch_instrument_metadata(
        self, *, native_symbols: Sequence[str]
    ) -> list[dict[str, Any]]: ...

    async def health(self) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


__all__ = ["ExchangeAdapter", "Kline", "SleepFn"]
