"""Binance spot REST adapter (section 4.5, design note 5.1).

Only this package knows Binance: URLs, interval tokens, page limits, payload
shape and error surface. Everything upstream sees the ExchangeAdapter protocol
and canonical rows.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

import httpx

from engine.data.bars import BAR_MS, Timeframe
from engine.data.instruments import MarketType
from engine.errors import DataUnavailableError, NotImplementedMarketError
from exchanges.base import Kline, SleepFn
from exchanges.binance.rate_limit import RateLimitPolicy, WeightWindowLimiter

#: Native timeframe -> Binance interval token; venue knowledge stays in-package.
INTERVAL_TOKENS: dict[Timeframe, str] = {
    Timeframe.D1: "1d",
    Timeframe.H4: "4h",
    Timeframe.H1: "1h",
}

#: Binance's maximum klines per request (design note 5.1).
MAX_PAGE_LIMIT = 1000
#: 429 backoff attempts before failing closed with DATA_UNAVAILABLE.
MAX_ATTEMPTS = 3

_KLINE_FIELDS = 12

_DEFAULT_POLICY = RateLimitPolicy()


class BinanceAdapter:
    """Binance public REST, spot only; perpetuals raise before any request (AC4)."""

    name: str = "binance"

    def __init__(
        self,
        *,
        market_type: MarketType = MarketType.SPOT,
        client: httpx.AsyncClient | None = None,
        policy: RateLimitPolicy = _DEFAULT_POLICY,
        now: Any = None,
        sleep: SleepFn = asyncio.sleep,
        base_url: str = "https://api.binance.com",
    ) -> None:
        if market_type is MarketType.PERPETUAL:
            raise NotImplementedMarketError(
                "perpetual market data is not served before M10 (section 4.8)",
                {"venue": "BINANCE", "market_type": "PERPETUAL"},
            )
        # Annotated so the attribute keeps the declared MarketType even though
        # the PERPETUAL branch above narrows the local (protocol compatibility).
        self.market_type: MarketType = market_type
        self._policy = policy
        self._sleep = sleep
        self._limiter = WeightWindowLimiter(
            budget=policy.budget,
            window_seconds=policy.window_seconds,
            now=now if now is not None else time.monotonic,
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

    async def fetch_historical_klines(
        self,
        *,
        native_symbol: str,
        timeframe: Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> list[Kline]:
        if timeframe is Timeframe.W1:
            raise ValueError(
                "1W bars are never fetched from the venue; resample them from 1D "
                "(section 4.3)"
            )
        if timeframe not in INTERVAL_TOKENS:
            raise ValueError(f"unsupported timeframe for klines: {timeframe!r}")
        if end_ms < start_ms:
            raise ValueError("end_ms must not precede start_ms")
        interval = INTERVAL_TOKENS[timeframe]
        interval_ms = BAR_MS[timeframe]

        klines: list[Kline] = []
        cursor = start_ms
        while True:
            payload = await self._get(
                "/api/v3/klines",
                {
                    "symbol": native_symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": MAX_PAGE_LIMIT,
                },
                weight=self._policy.klines_weight,
            )
            rows = self._parse_klines(payload, interval_ms=interval_ms)
            if not rows:
                break
            page = [row for row in rows if row.open_time <= end_ms]
            klines.extend(page)
            last_open = rows[-1].open_time
            if len(rows) < MAX_PAGE_LIMIT or last_open >= end_ms or len(page) < len(rows):
                break
            next_cursor = last_open + 1
            if next_cursor <= cursor:  # pragma: no cover - ascending check precludes it
                raise DataUnavailableError(
                    "binance kline pagination made no progress",
                    {"reason": "pagination", "symbol": native_symbol},
                )
            cursor = next_cursor
        return klines

    async def fetch_instrument_metadata(
        self, *, native_symbols: Sequence[str]
    ) -> list[dict[str, Any]]:
        if not native_symbols:
            return []
        payload = await self._get(
            "/api/v3/exchangeInfo",
            {},
            weight=self._policy.exchange_info_weight,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
            raise DataUnavailableError(
                "binance exchangeInfo payload has an unexpected shape",
                {"reason": "shape"},
            )
        listed: dict[str, Any] = {}
        for entry in payload["symbols"]:
            if isinstance(entry, dict) and isinstance(entry.get("symbol"), str):
                listed[entry["symbol"]] = entry
        metadata: list[dict[str, Any]] = []
        for native_symbol in native_symbols:
            entry = listed.get(native_symbol)
            if entry is None:
                raise DataUnavailableError(
                    f"instrument {native_symbol} is not listed on binance",
                    {"reason": "not_listed", "native_symbol": native_symbol},
                )
            status = entry.get("status")
            if status != "TRADING":
                raise DataUnavailableError(
                    f"instrument {native_symbol} is not TRADING (status {status!r})",
                    {"reason": "not_trading", "native_symbol": native_symbol},
                )
            metadata.append(
                {
                    "native_symbol": native_symbol,
                    "status": status,
                    "base_asset": entry.get("baseAsset"),
                    "quote_asset": entry.get("quoteAsset"),
                    "onboard_date_ms": entry.get("onboardDate"),
                    "tick_size": _filter_value(entry, "PRICE_FILTER", "tickSize"),
                    "step_size": _filter_value(entry, "LOT_SIZE", "stepSize"),
                }
            )
        return metadata

    async def health(self) -> dict[str, Any]:
        payload = await self._get("/api/v3/time", {}, weight=self._policy.time_weight)
        if not isinstance(payload, dict) or "serverTime" not in payload:
            raise DataUnavailableError(
                "binance time payload has an unexpected shape",
                {"reason": "shape"},
            )
        try:
            server_time_ms = int(payload["serverTime"])
        except (TypeError, ValueError) as cause:
            raise DataUnavailableError(
                "binance time payload could not be parsed",
                {"reason": "row"},
            ) from cause
        return {"ok": True, "server_time_ms": server_time_ms}

    async def _get(
        self,
        path: str,
        params: dict[str, str | int],
        *,
        weight: int,
    ) -> Any:
        """One weighted request: 429 backs off, everything else fails closed."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._limiter.acquire(weight)
            try:
                response = await self._client.get(path, params=params)
            except httpx.HTTPError as cause:
                raise DataUnavailableError(
                    "binance request failed",
                    {
                        "reason": "transport",
                        "endpoint": path,
                        "type": type(cause).__name__,
                    },
                ) from cause
            if response.status_code == 429 and attempt < MAX_ATTEMPTS:
                await self._sleep(_retry_after_seconds(response))
                continue
            if response.status_code != 200:
                raise DataUnavailableError(
                    "binance request failed",
                    {"reason": "http", "endpoint": path, "status": response.status_code},
                )
            try:
                return response.json()
            except ValueError as cause:
                raise DataUnavailableError(
                    "binance response was not JSON",
                    {"reason": "shape", "endpoint": path},
                ) from cause
        raise DataUnavailableError(  # pragma: no cover - loop always returns/raises above
            "binance kept rate-limiting the client",
            {"reason": "rate_limited", "endpoint": path, "status": 429},
        )

    @staticmethod
    def _parse_klines(payload: Any, *, interval_ms: int) -> list[Kline]:
        if not isinstance(payload, list):
            raise DataUnavailableError(
                "binance klines payload has an unexpected shape",
                {"reason": "shape", "type": type(payload).__name__},
            )
        klines: list[Kline] = []
        previous_open: int | None = None
        for index, row in enumerate(payload):
            if not isinstance(row, list) or len(row) != _KLINE_FIELDS:
                raise DataUnavailableError(
                    "binance kline row has an unexpected shape",
                    {"reason": "row", "index": index},
                )
            try:
                open_time = int(row[0])
                parsed = Kline(
                    open_time=open_time,
                    close_time=open_time + interval_ms,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    quote_volume=float(row[7]),
                    trades=int(row[8]),
                )
            except (TypeError, ValueError) as cause:
                raise DataUnavailableError(
                    "binance kline row could not be parsed",
                    {"reason": "row", "index": index},
                ) from cause
            if previous_open is not None and open_time <= previous_open:
                raise DataUnavailableError(
                    "binance kline rows are not strictly ascending",
                    {"reason": "row", "index": index},
                )
            previous_open = open_time
            klines.append(parsed)
        return klines


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return 1.0
    try:
        return float(raw)
    except ValueError:
        return 1.0


def _filter_value(entry: dict[str, Any], filter_type: str, key: str) -> float | None:
    filters = entry.get("filters")
    if not isinstance(filters, list):
        return None
    for entry_filter in filters:
        if (
            isinstance(entry_filter, dict)
            and entry_filter.get("filterType") == filter_type
        ):
            try:
                raw_value = entry_filter.get(key)
                if raw_value is None:
                    return None
                return float(raw_value)
            except (TypeError, ValueError):
                return None
    return None
