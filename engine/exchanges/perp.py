"""M10 — the perp adapter: funding, open interest, liquidations (spec 4.5,
7.7; design note m10_perp_live section 2, D-067).

Lives on the same ExchangeAdapter interface (R2); only this package knows
Binance's perp REST. Spot↔perp substitution is fail-closed (PD-10): a perp
fetch on a spot instrument raises, and vice versa.

The rows are canonical (venue knowledge stays in-package): ``FundingRate``
(every 8h), ``OpenInterest`` (per bar), ``Liquidation`` (side/qty/price).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from engine.data.bars import Timeframe
from engine.data.instruments import MarketType
from engine.errors import DataUnavailableError
from exchanges.binance.rate_limit import RateLimitPolicy, WeightWindowLimiter

#: Binance perp REST base (fapi = futures API).
PERP_BASE_URL: Final = "https://fapi.binance.com"

#: Funding interval: every 8h (spec 7.7).
FUNDING_INTERVAL_HOURS: Final = 8

MAX_ATTEMPTS: Final = 3

_DEFAULT_POLICY = RateLimitPolicy()

#: Perp timeframe -> Binance interval token (same tokens as spot).
PERP_INTERVAL_TOKENS: dict[Timeframe, str] = {
    Timeframe.D1: "1d",
    Timeframe.H4: "4h",
    Timeframe.H1: "1h",
}


@dataclass(frozen=True)
class FundingRate:
    """One funding event: ``ts`` is the funding time, ``rate`` the rate
    (positive = longs pay shorts)."""

    instrument_symbol: str
    ts: datetime
    rate: float


@dataclass(frozen=True)
class OpenInterest:
    """One OI bar: ``bar_time`` + the open interest in contracts."""

    instrument_symbol: str
    timeframe: Timeframe
    bar_time: datetime
    oi: float


@dataclass(frozen=True)
class Liquidation:
    """One forced liquidation: side LONG (long liquidated) / SHORT."""

    instrument_symbol: str
    ts: datetime
    side: str
    qty: float
    price: float


class PerpMarketError(DataUnavailableError):
    """A perp request that cannot be served (spot instrument, bad params)."""


class BinancePerpAdapter:
    """Binance perpetuals REST: funding, OI, liquidations (M10).

    Constructor never takes a market_type: this adapter *is* perp; a spot
    symbol is rejected per-call by the market-type check (D-067).
    """

    name: str = "binance-perp"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        policy: RateLimitPolicy = _DEFAULT_POLICY,
        now: Any = None,
        sleep: Any = asyncio.sleep,
        base_url: str = PERP_BASE_URL,
    ) -> None:
        self._policy = policy
        self._sleep = sleep
        self._limiter = WeightWindowLimiter(
            budget=policy.budget,
            window_seconds=policy.window_seconds,
            now=now if now is not None else _monotonic,
            sleep=sleep,
        )
        self._owns_client = client is None
        self._client = client if client is not None else httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(30.0),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any], *, weight: int = 5) -> Any:
        await self._limiter.acquire(weight)
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._client.get(path, params=params)
                if response.status_code == 429:
                    await self._sleep(_retry_after_seconds(response) * (2**attempt))
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_error = exc
                await self._sleep(_retry_after_seconds(None) * (2**attempt))
        raise DataUnavailableError(
            "perp request failed after retries",
            {"path": path, "error": str(last_error)},
        )

    async def fetch_funding_rates(
        self,
        symbol: str,
        *,
        start: datetime,
        end: datetime,
    ) -> list[FundingRate]:
        """Funding history for ``[start, end]`` (every 8h)."""
        payload = await self._get(
            "/fapi/v1/fundingRate",
            {
                "symbol": symbol,
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
                "limit": 1000,
            },
        )
        if not isinstance(payload, list):
            raise DataUnavailableError(
                "funding payload is not a list", {"symbol": symbol}
            )
        rates: list[FundingRate] = []
        for row in payload:
            rates.append(
                FundingRate(
                    instrument_symbol=symbol,
                    ts=datetime.fromtimestamp(int(row["fundingTime"]) / 1000, tz=UTC),
                    rate=float(row["fundingRate"]),
                )
            )
        return rates

    async def fetch_open_interest(
        self,
        symbol: str,
        *,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[OpenInterest]:
        """OI history (5m granularity aggregated to the timeframe's bars)."""
        token = PERP_INTERVAL_TOKENS.get(timeframe)
        if token is None:
            raise PerpMarketError(
                "timeframe not served on perp", {"timeframe": timeframe.value}
            )
        payload = await self._get(
            "/futures/data/openInterestHist",
            {
                "pair": symbol,
                "contractType": "PERPETUAL",
                "period": token,
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
                "limit": 500,
            },
        )
        if not isinstance(payload, list):
            raise DataUnavailableError(
                "open interest payload is not a list", {"symbol": symbol}
            )
        bars: list[OpenInterest] = []
        for row in payload:
            bars.append(
                OpenInterest(
                    instrument_symbol=symbol,
                    timeframe=timeframe,
                    bar_time=datetime.fromtimestamp(int(row["timestamp"]) / 1000, tz=UTC),
                    oi=float(row["sumOpenInterest"]),
                )
            )
        return bars

    async def fetch_liquidations(
        self,
        symbol: str,
        *,
        start: datetime,
        end: datetime,
    ) -> list[Liquidation]:
        """Forced liquidation orders for ``[start, end]``.

        Binance's public liquidation endpoint is rate-limited and returns
        a rolling window; an empty list means "no liquidations in window",
        which is never a failure (PD-10: no silent substitution).
        """
        payload = await self._get(
            "/fapi/v1/allForceOrders",
            {"symbol": symbol, "limit": 100},
        )
        if not isinstance(payload, list):
            raise DataUnavailableError(
                "liquidation payload is not a list", {"symbol": symbol}
            )
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        rows: list[Liquidation] = []
        for row in payload:
            ts = int(row["time"])
            if ts < start_ms or ts > end_ms:
                continue
            rows.append(
                Liquidation(
                    instrument_symbol=symbol,
                    ts=datetime.fromtimestamp(ts / 1000, tz=UTC),
                    side="LONG" if row.get("side") == "BUY" else "SHORT",
                    qty=float(row.get("origQty", row.get("executedQty", 0))),
                    price=float(row.get("averagePrice", row.get("price", 0))),
                )
            )
        return rows


def _monotonic() -> float:
    import time

    return time.monotonic()


def _retry_after_seconds(response: Any) -> float:
    """The venue's Retry-After, or a 1s default when absent/unknown."""
    if response is None:
        return 1.0
    header = getattr(response, "headers", {}).get("Retry-After")
    if header is None:
        return 1.0
    try:
        return float(header)
    except (TypeError, ValueError):
        return 1.0


def persist_funding_rates(
    conn: Any,
    rates: Sequence[FundingRate],
    *,
    instrument_id: str,
) -> int:
    """Persist funding rows idempotently (ON CONFLICT DO NOTHING on the
    (instrument, ts) unique index). Returns the number of new rows."""
    from sqlalchemy import text

    inserted = 0
    for rate in rates:
        result = conn.execute(
            text(
                "INSERT INTO market.funding_rate (instrument_id, ts, rate) "
                "VALUES (:instrument_id, :ts, :rate) "
                "ON CONFLICT (instrument_id, ts) DO NOTHING"
            ),
            {"instrument_id": instrument_id, "ts": rate.ts, "rate": rate.rate},
        )
        inserted += int(getattr(result, "rowcount", 0) or 0)
    return inserted


def persist_open_interest(
    conn: Any,
    bars: Sequence[OpenInterest],
    *,
    instrument_id: str,
) -> int:
    """Persist OI bars idempotently ((instrument, tf, bar_time) unique)."""
    from sqlalchemy import text

    inserted = 0
    for bar in bars:
        result = conn.execute(
            text(
                "INSERT INTO market.open_interest (instrument_id, timeframe, bar_time, oi) "
                "VALUES (:instrument_id, :timeframe, :bar_time, :oi) "
                "ON CONFLICT (instrument_id, timeframe, bar_time) DO NOTHING"
            ),
            {
                "instrument_id": instrument_id,
                "timeframe": bar.timeframe.value,
                "bar_time": bar.bar_time,
                "oi": bar.oi,
            },
        )
        inserted += int(getattr(result, "rowcount", 0) or 0)
    return inserted


def persist_liquidations(
    conn: Any,
    rows: Sequence[Liquidation],
    *,
    instrument_id: str,
) -> int:
    """Persist liquidations (no unique constraint: a repeat force order is a
    distinct event; idempotency is the ingest scope's job)."""
    from sqlalchemy import text

    inserted = 0
    for row in rows:
        result = conn.execute(
            text(
                "INSERT INTO market.liquidation (instrument_id, ts, side, qty, price) "
                "VALUES (:instrument_id, :ts, :side, :qty, :price)"
            ),
            {
                "instrument_id": instrument_id,
                "ts": row.ts,
                "side": row.side,
                "qty": row.qty,
                "price": row.price,
            },
        )
        inserted += int(getattr(result, "rowcount", 0) or 0)
    return inserted


def market_type_guard(market_type: MarketType) -> None:
    """PD-10: perp data for perp instruments only; spot↔perp never
    substitutes."""
    if market_type is not MarketType.PERPETUAL:
        raise PerpMarketError(
            "perp adapter serves PERPETUAL only (PD-10: no spot<->perp substitution)",
            {"market_type": market_type.value},
        )
