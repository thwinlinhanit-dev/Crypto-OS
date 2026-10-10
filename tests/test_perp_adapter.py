"""M10 — the perp adapter tests (spec 4.5/7.7, design note m10_perp_live
section 2, D-067).

Deterministic: an httpx MockTransport feeds the payloads; the clock and
sleep are injected, so no network is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from engine.data.bars import Timeframe
from engine.data.instruments import MarketType
from engine.exchanges.perp import (
    PERP_BASE_URL,
    BinancePerpAdapter,
    FundingRate,
    OpenInterest,
    PerpMarketError,
    market_type_guard,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)


def _adapter(payload: Any) -> BinancePerpAdapter:
    """A perp adapter whose transport answers every GET with ``payload``."""
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(
        base_url=PERP_BASE_URL, transport=httpx.MockTransport(_handler)
    )
    return BinancePerpAdapter(
        client=client,
        now=lambda: 0.0,
        sleep=lambda _seconds: _noop_sleep(),
    )


async def _noop_sleep() -> None:
    return None


FUNDING_PAYLOAD = [
    {"fundingTime": int(T0.timestamp() * 1000), "fundingRate": "0.00010000"},
    {"fundingTime": int(T1.timestamp() * 1000), "fundingRate": "-0.00005000"},
]

OI_PAYLOAD = [
    {"timestamp": int(T0.timestamp() * 1000), "sumOpenInterest": "1234.5"},
    {"timestamp": int(T1.timestamp() * 1000), "sumOpenInterest": "1500.0"},
]

LIQUIDATION_PAYLOAD = [
    {"time": int(T0.timestamp() * 1000), "side": "BUY", "origQty": "2.0",
     "averagePrice": "60000.0"},
    {"time": int(T1.timestamp() * 1000), "side": "SELL", "origQty": "1.5",
     "averagePrice": "59000.0"},
]


@pytest.mark.asyncio
async def test_fetch_funding_rates() -> None:
    adapter = _adapter(FUNDING_PAYLOAD)
    rates = await adapter.fetch_funding_rates("BTCUSDT", start=T0, end=T1)
    assert len(rates) == 2
    assert rates[0] == FundingRate("BTCUSDT", T0, 0.0001)
    assert rates[1].rate == -0.00005
    await adapter.aclose()


@pytest.mark.asyncio
async def test_fetch_open_interest() -> None:
    adapter = _adapter(OI_PAYLOAD)
    bars = await adapter.fetch_open_interest(
        "BTCUSDT", timeframe=Timeframe.D1, start=T0, end=T1
    )
    assert len(bars) == 2
    assert isinstance(bars[0], OpenInterest)
    assert bars[0].oi == 1234.5
    await adapter.aclose()


@pytest.mark.asyncio
async def test_fetch_liquidations_filters_by_window() -> None:
    """Rows outside [start, end] are dropped (the rolling window is wider)."""
    adapter = _adapter(LIQUIDATION_PAYLOAD)
    rows = await adapter.fetch_liquidations("BTCUSDT", start=T0, end=T1)
    assert len(rows) == 2
    # side mapping: a BUY force order liquidates a LONG (binance's side is
    # the order side)
    assert rows[0].side == "LONG"
    assert rows[1].side == "SHORT"
    await adapter.aclose()


@pytest.mark.asyncio
async def test_fetch_liquidations_empty_window_is_not_a_failure() -> None:
    """PD-10: an empty window is "no liquidations", never a substitution."""
    adapter = _adapter([])
    rows = await adapter.fetch_liquidations(
        "BTCUSDT",
        start=datetime(2030, 1, 1, tzinfo=UTC),
        end=datetime(2030, 1, 2, tzinfo=UTC),
    )
    assert rows == []
    await adapter.aclose()


@pytest.mark.asyncio
async def test_a_non_list_payload_fails_closed() -> None:
    from engine.errors import DataUnavailableError

    adapter = _adapter({"error": "bad"})
    with pytest.raises(DataUnavailableError):
        await adapter.fetch_funding_rates("BTCUSDT", start=T0, end=T1)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_a_429_then_success_succeeds() -> None:
    """Rate-limit aware (R6): one 429 then the payload."""
    calls = {"count": 0}

    async def _handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(200, json=FUNDING_PAYLOAD)

    client = httpx.AsyncClient(
        base_url=PERP_BASE_URL, transport=httpx.MockTransport(_handler)
    )
    adapter = BinancePerpAdapter(client=client, now=lambda: 0.0)
    rates = await adapter.fetch_funding_rates("BTCUSDT", start=T0, end=T1)
    assert len(rates) == 2
    assert calls["count"] == 2
    await adapter.aclose()


@pytest.mark.asyncio
async def test_persistent_429_fails_closed() -> None:
    from engine.errors import DataUnavailableError

    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0.001"})

    client = httpx.AsyncClient(
        base_url=PERP_BASE_URL, transport=httpx.MockTransport(_handler)
    )
    adapter = BinancePerpAdapter(
        client=client, now=lambda: 0.0, sleep=lambda _s: _noop_sleep()
    )
    with pytest.raises(DataUnavailableError):
        await adapter.fetch_funding_rates("BTCUSDT", start=T0, end=T1)
    await adapter.aclose()


def test_market_type_guard_rejects_spot() -> None:
    """PD-10: perp data for perp only; a spot request fails closed."""
    with pytest.raises(PerpMarketError):
        market_type_guard(MarketType.SPOT)


def test_market_type_guard_allows_perpetual() -> None:
    market_type_guard(MarketType.PERPETUAL)
