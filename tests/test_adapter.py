"""Spec section 4.5 / T1.1: the Binance spot adapter behind ExchangeAdapter.

All HTTP is httpx MockTransport - no network in tests (design note 5.1).
Covers: pagination, canonical close_time, rate limiting, 429 backoff,
PERPETUAL -> NOT_IMPLEMENTED, and every failure surfacing as DATA_UNAVAILABLE.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import httpx
import pytest

from engine.data.bars import BAR_MS, Timeframe
from engine.data.instruments import MarketType
from engine.errors import DataUnavailableError, NotImplementedMarketError
from exchanges.binance import BinanceAdapter
from exchanges.binance.rate_limit import RateLimitPolicy, WeightWindowLimiter

BASE_URL = "https://api.binance.com"
HOUR_MS = 3_600_000


def _venue_row(open_ms: int, interval_ms: int) -> list[str]:
    """A 12-field kline row as Binance returns it (all values are JSON strings)."""
    open_price = 100.0 + (open_ms // HOUR_MS) % 50
    return [
        str(open_ms),
        f"{open_price:.2f}",
        f"{open_price + 5:.2f}",
        f"{open_price - 5:.2f}",
        f"{open_price + 1:.2f}",
        "12.5",
        str(open_ms + interval_ms - 1),  # venue close_time: inclusive last ms
        "1250.0",
        "34",
        "6.0",
        "600.0",
        "0",
    ]


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE_URL)


def _klines_handler(
    calls: list[httpx.Request],
    *,
    total: int,
    interval: str = "1h",
    interval_ms: int = HOUR_MS,
) -> Any:
    """Serve ``total`` klines, paginated by startTime, Binance-style."""

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/api/v3/klines"
        assert request.url.params["symbol"] == "BTCUSDT"
        assert request.url.params["interval"] == interval
        limit = int(request.url.params["limit"])
        assert limit == 1000
        start = int(request.url.params["startTime"])
        end = int(request.url.params["endTime"])
        rows: list[list[str]] = []
        for index in range(total):
            open_ms = index * interval_ms
            if open_ms < start:
                continue
            if open_ms > end:
                break
            rows.append(_venue_row(open_ms, interval_ms))
            if len(rows) == limit:
                break
        return httpx.Response(200, json=rows)

    return handle


def _adapter(
    handler: Any,
    *,
    policy: RateLimitPolicy | None = None,
    clock: Any = None,
    sleep: Any = None,
    market_type: MarketType = MarketType.SPOT,
) -> BinanceAdapter:
    kwargs: dict[str, Any] = {"client": _client(handler), "market_type": market_type}
    if policy is not None:
        kwargs["policy"] = policy
    if clock is not None:
        kwargs["now"] = clock
    if sleep is not None:
        kwargs["sleep"] = sleep
    return BinanceAdapter(**kwargs)


async def test_fetch_pages_until_the_range_is_covered() -> None:
    calls: list[httpx.Request] = []
    adapter = _adapter(_klines_handler(calls, total=2500))
    klines = await adapter.fetch_historical_klines(
        native_symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        start_ms=0,
        end_ms=2500 * HOUR_MS,
    )
    assert len(klines) == 2500
    assert len(calls) == 3  # 1000 + 1000 + 500
    # cursor = last page's last open + 1 ms, so pages never overlap
    assert [int(c.url.params["startTime"]) for c in calls] == [
        0,
        999 * HOUR_MS + 1,
        1999 * HOUR_MS + 1,
    ]
    opens = [kline.open_time for kline in klines]
    assert opens == sorted(set(opens))  # strictly ascending, no page overlap
    assert opens[0] == 0 and opens[-1] == 2499 * HOUR_MS


async def test_close_time_is_the_exclusive_bar_boundary() -> None:
    adapter = _adapter(_klines_handler([], total=1))
    klines = await adapter.fetch_historical_klines(
        native_symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        start_ms=0,
        end_ms=HOUR_MS,
    )
    assert len(klines) == 1
    # Venue sent open+interval-1; the canonical row carries open+interval (D-008).
    assert klines[0].close_time == klines[0].open_time + BAR_MS[Timeframe.H1]
    assert klines[0].trades == 34
    assert klines[0].volume == 12.5
    assert klines[0].quote_volume == 1250.0


async def test_fetch_parses_daily_klines_to_typed_numbers() -> None:
    adapter = _adapter(
        _klines_handler([], total=1, interval="1d", interval_ms=BAR_MS[Timeframe.D1]),
    )
    klines = await adapter.fetch_historical_klines(
        native_symbol="BTCUSDT",
        timeframe=Timeframe.D1,
        start_ms=0,
        end_ms=BAR_MS[Timeframe.D1],
    )
    payload = json.loads(json.dumps(asdict(klines[0])))
    assert payload["open_time"] == 0
    assert payload["close_time"] == BAR_MS[Timeframe.D1]
    assert isinstance(payload["open"], float)
    assert isinstance(payload["trades"], int)


async def test_weekly_timeframes_are_never_fetched() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(ValueError, match="1W"):
        await adapter.fetch_historical_klines(
            native_symbol="BTCUSDT",
            timeframe=Timeframe.W1,
            start_ms=0,
            end_ms=HOUR_MS,
        )


def test_perpetual_construction_fails_closed() -> None:
    with pytest.raises(NotImplementedMarketError) as exc:
        BinanceAdapter(market_type=MarketType.PERPETUAL)
    assert exc.value.code == "NOT_IMPLEMENTED"
    assert exc.value.http_status == 501


@pytest.mark.parametrize("failure", [500, 403])
async def test_http_failures_surface_as_data_unavailable(failure: int) -> None:
    adapter = _adapter(lambda request: httpx.Response(failure, text="upstream broke"))
    with pytest.raises(DataUnavailableError) as exc:
        await adapter.fetch_historical_klines(
            native_symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            start_ms=0,
            end_ms=HOUR_MS,
        )
    assert exc.value.code == "DATA_UNAVAILABLE"
    assert exc.value.detail["status"] == failure


async def test_transport_errors_surface_as_data_unavailable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    adapter = _adapter(handle)
    with pytest.raises(DataUnavailableError) as exc:
        await adapter.fetch_historical_klines(
            native_symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            start_ms=0,
            end_ms=HOUR_MS,
        )
    assert exc.value.code == "DATA_UNAVAILABLE"
    assert exc.value.detail["reason"] == "transport"


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"not": "a list"}, "shape"),
        ([["1690000000000", "not-a-number"]], "row"),
        ([["1690000000000", "oops"] + ["1"] * 10], "row"),
    ],
)
async def test_malformed_payloads_fail_closed(payload: Any, match: str) -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(DataUnavailableError, match=match):
        await adapter.fetch_historical_klines(
            native_symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            start_ms=0,
            end_ms=HOUR_MS,
        )


async def test_http_429_is_backed_off_and_retried() -> None:
    attempts: list[int] = []
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    def handle(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={})
        return httpx.Response(200, json=[_venue_row(0, HOUR_MS)])

    adapter = _adapter(handle, sleep=fake_sleep)
    klines = await adapter.fetch_historical_klines(
        native_symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        start_ms=0,
        end_ms=HOUR_MS,
    )
    assert len(klines) == 1
    assert len(attempts) == 2
    assert slept == [2.0]


async def test_repeated_429_gives_up_with_data_unavailable() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    adapter = _adapter(
        lambda request: httpx.Response(429, headers={"Retry-After": "1"}, json={}),
        sleep=fake_sleep,
    )
    with pytest.raises(DataUnavailableError) as exc:
        await adapter.fetch_historical_klines(
            native_symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            start_ms=0,
            end_ms=HOUR_MS,
        )
    assert exc.value.detail["status"] == 429
    assert len(slept) == 2  # attempts 2 and 3 slept; attempt 1 is the first try


async def test_weight_window_limiter_sleeps_until_the_budget_frees() -> None:
    now = [0.0]
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = WeightWindowLimiter(
        budget=4,
        window_seconds=60.0,
        now=lambda: now[0],
        sleep=fake_sleep,
    )
    await limiter.acquire(2)
    await limiter.acquire(2)
    await limiter.acquire(2)  # budget full: must wait out the window
    assert slept == [60.0]


async def test_adapter_applies_the_rate_limit_policy() -> None:
    now = [0.0]
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    calls: list[httpx.Request] = []
    # 2001 hourly bars -> 3 kline pages; budget 4 with weight 2 -> 3rd page waits.
    adapter = _adapter(
        _klines_handler(calls, total=2001),
        policy=RateLimitPolicy(budget=4),
        clock=lambda: now[0],
        sleep=fake_sleep,
    )
    klines = await adapter.fetch_historical_klines(
        native_symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        start_ms=0,
        end_ms=2001 * HOUR_MS,
    )
    assert len(klines) == 2001
    assert len(calls) == 3
    assert slept == [60.0]


async def test_health_reports_the_server_time() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/time"
        return httpx.Response(200, json={"serverTime": 1_700_000_000_000})

    adapter = _adapter(handle)
    health = await adapter.health()
    assert health == {"ok": True, "server_time_ms": 1_700_000_000_000}


async def test_instrument_metadata_parses_exchange_info() -> None:
    payload = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "onboardDate": 1_504_771_200_000,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.00000001"},
                ],
            },
            {"symbol": "DEADUSDT", "status": "BREAK", "baseAsset": "DEAD", "quoteAsset": "USDT"},
        ]
    }

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/exchangeInfo"
        return httpx.Response(200, json=payload)

    adapter = _adapter(handle)
    metadata = await adapter.fetch_instrument_metadata(native_symbols=["BTCUSDT"])
    assert metadata == [
        {
            "native_symbol": "BTCUSDT",
            "status": "TRADING",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "onboard_date_ms": 1_504_771_200_000,
            "tick_size": 0.01,
            "step_size": 0.00000001,
        }
    ]


async def test_instrument_metadata_rejects_a_non_trading_symbol() -> None:
    payload = {"symbols": [{"symbol": "DEADUSDT", "status": "BREAK"}]}

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    adapter = _adapter(handle)
    with pytest.raises(DataUnavailableError, match="TRADING"):
        await adapter.fetch_instrument_metadata(native_symbols=["DEADUSDT"])
