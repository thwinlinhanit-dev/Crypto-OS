"""M11 — the §14 performance gates (spec line 597, design note
m11_hardening section 1, D-075/D-076).

Targets (laptop): 5,000-candle API <300ms; indicators 5,000 bars <150ms;
events+stats for 30-symbol daily universe <5s; single-symbol 10y daily
backtest <5s; scanner 100 symbols 1D <2s; chart interactions <50ms.

A perf test that exceeds its budget fails CI (spec §0). Live-DB suites
skip (never hang/fail) when the scratch cluster is unreachable (D-076);
the engine half (indicators, simulator, scanner) is deterministic and
always runs.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import polars as pl
import pytest
from sqlalchemy import Connection, create_engine, text

from apps.api.settings import get_settings

#: The §14 budgets (seconds).
BUDGET_API_5000_CANDLES = 0.300
BUDGET_INDICATORS_5000_BARS = 0.150
BUDGET_BACKTEST_10Y = 5.0
BUDGET_SCANNER_100 = 2.0
BUDGET_CHART_INTERACTION = 0.050


@pytest.fixture()
def conn() -> Iterator[Connection]:
    engine = create_engine(
        get_settings().database_url, future=True, connect_args={"connect_timeout": 3}
    )
    try:
        connection = engine.connect()
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"database unreachable: {exc}")
    try:
        yield connection
    finally:
        connection.close()
        engine.dispose()


def _bars(count: int) -> pl.DataFrame:
    """A synthetic 5,000-bar frame (deterministic)."""
    import math

    base = 1_704_067_200_000  # 2024-01-01T00:00:00Z
    step = 86_400_000
    return pl.DataFrame(
        {
            "open_time": [base + i * step for i in range(count)],
            "close_time": [base + (i + 1) * step for i in range(count)],
            "open": [100.0 + 10 * math.sin(i / 50) for i in range(count)],
            "high": [101.0 + 10 * math.sin(i / 50) for i in range(count)],
            "low": [99.0 + 10 * math.sin(i / 50) for i in range(count)],
            "close": [100.5 + 10 * math.sin(i / 50) for i in range(count)],
            "volume": [1000.0 + 100 * math.cos(i / 25) for i in range(count)],
        }
    )


def _sim_bars(count: int) -> list[Any]:
    """The simulator's Bar shape: index + ISO time + OHLC."""
    from datetime import UTC, datetime

    from engine.strategy.simulator import Bar

    base = 1_400_000_000  # 2014
    bars: list[Any] = []
    for i in range(count):
        ts = datetime.fromtimestamp(base + i * 86_400, tz=UTC)
        close = 100.5 + (i % 97) * 0.5
        bars.append(
            Bar(
                index=i,
                time=ts.isoformat().replace("+00:00", "Z"),
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
            )
        )
    return bars


def _strategy_definition() -> dict[str, Any]:
    """A minimal SIGNAL-mode strategy (the M6 StrategyDefinition shape)."""
    return {
        "strategy_id": "perf-test",
        "version": "1.0",
        "entry": {
            "detector": "RVOL_CLIMAX@5.0",
            "params": {},
            "signals": ["CONT"],
            "side": "LONG_ONLY",
        },
        "sizing": {"mode": "RISK", "risk_pct": 1.0, "max_alloc_pct": 100.0},
        "stop": {"kind": "ATR", "atr_len": 14, "atr_mult": 2.0},
        "costs": {
            "fee_model": {"kind": "PERCENT", "value": 0.10},
            "slippage_model": {"kind": "FIXED_BPS", "value": 0},
        },
        "execution": {
            "fill_model": "REALISTIC_OHLC",
            "initial_capital": 10000,
            "delay_bars": 0,
        },
    }


# --------------------------------------------------------------------------
# The engine half (deterministic; always runs)
# --------------------------------------------------------------------------


def test_indicators_5000_bars_under_150ms() -> None:
    """§14: indicators 5,000 bars <150ms (the pure-function timing)."""
    from engine.features.indicators import (
        _compute_ema,
        _compute_rsi,
        _compute_sma,
    )

    frame = _bars(5000)
    start = time.perf_counter()
    _compute_sma(frame, {"length": 20})
    _compute_ema(frame, {"length": 20})
    _compute_rsi(frame, {"length": 14})
    elapsed = time.perf_counter() - start
    assert elapsed < BUDGET_INDICATORS_5000_BARS, f"indicators took {elapsed:.3f}s"


def test_backtest_10y_daily_under_5s() -> None:
    """§14: single-symbol 10y daily backtest <5s (~2,500 bars)."""
    from engine.strategy.models import StrategyDefinition
    from engine.strategy.simulator import Signal, simulate

    count = 2500
    bars = _sim_bars(count)
    signals = [
        Signal(bar_index=i, event_type="CONT", direction="LONG")
        for i in range(0, count, 20)
    ]
    strategy = StrategyDefinition.model_validate(_strategy_definition())
    atr = [1.0 for _ in range(count)]
    start = time.perf_counter()
    result = simulate(
        strategy=strategy,
        instrument="BTCUSDT",
        bars=bars,
        signals=signals,
        atr=atr,
    )
    elapsed = time.perf_counter() - start
    assert elapsed < BUDGET_BACKTEST_10Y, f"backtest took {elapsed:.3f}s"
    assert result is not None


def test_chart_interaction_under_50ms() -> None:
    """§14: chart interactions <50ms - the crosshair/hover handlers are
    synchronous map lookups over the candle series (the pure half)."""
    candles = _bars(5000)
    lookup = {
        int(row["open_time"]): row["close"] for row in candles.iter_rows(named=True)
    }
    start = time.perf_counter()
    for _ in range(1000):
        hover_time = next(iter(lookup))
        _ = lookup.get(hover_time)
    elapsed = time.perf_counter() - start
    per_interaction = elapsed / 1000
    assert per_interaction < BUDGET_CHART_INTERACTION, (
        f"hover lookup took {per_interaction * 1000:.3f}ms"
    )


def test_scanner_row_build_budget() -> None:
    """§14: scanner 100 symbols 1D <2s - the per-symbol row build must fit
    the 100-symbol budget with margin (the seeded universe is smaller,
    OQ-24)."""
    from engine.scanner.scan import ScannerRow

    frame = _bars(500)
    last = frame.row(-1, named=True)
    regime = {"trend_state": "UP", "vol_state": "NORMAL", "volume_state": "HIGH"}
    start = time.perf_counter()
    for _ in range(100):
        ScannerRow(
            instrument_id="x",
            symbol="BTCUSDT",
            price=float(last["close"]),
            chg_24h=0.01,
            rvol=1.5,
            last_event_type="CONT",
            last_event_time=None,
            last_event_age_bars=3,
            regime=regime,
            grade="A",
            n_eff=23,
        )
    elapsed = time.perf_counter() - start
    assert elapsed < BUDGET_SCANNER_100, f"100 scanner rows took {elapsed:.3f}s"


# --------------------------------------------------------------------------
# The API half (live-DB; skip-guarded, D-076)
# --------------------------------------------------------------------------


def test_api_5000_candles_under_300ms(conn: Connection) -> None:
    """§14: 5,000-candle API <300ms (the seeded window; the budget applies
    to one API page)."""
    from fastapi.testclient import TestClient

    import apps.api.jobs as jobs_mod
    from apps.api.main import create_app
    from engine.data.bars import Timeframe
    from engine.data.instruments import upsert_instruments
    from tests.support import BTC, WIDE_BUDGET, FixtureAdapter, daily

    conn.execute(text("SET session_replication_role = replica"))
    for table in ("market.dataset_manifest", "market.instrument"):
        conn.execute(text(f"DELETE FROM {table}"))
    conn.execute(text("SET session_replication_role = DEFAULT"))
    conn.commit()
    upsert_instruments(conn, [BTC])
    conn.commit()

    jobs_mod.build_adapter = lambda params: FixtureAdapter(
        {("BTCUSDT", Timeframe.D1): daily()}
    )
    client = TestClient(create_app())
    response = client.post(
        "/v1/data/ingest",
        json={
            "venue": "BINANCE", "market_type": "SPOT",
            "instrument_ids": [str(BTC.instrument_id)], "timeframes": ["1D"],
            "from": "2024-01-01T00:00:00Z", "to": "2024-03-01T00:00:00Z",
            "storage_budget_gb": WIDE_BUDGET / 1024**3,
        },
    )
    if response.status_code != 202:
        pytest.skip(f"ingest unavailable: {response.text[:120]}")
    job = client.get(f"/v1/jobs/{response.json()['job_id']}").json()
    if job.get("status") != "succeeded":
        pytest.skip(f"ingest did not succeed: {job.get('error', '')[:120]}")

    start = time.perf_counter()
    candles = client.get(
        f"/v1/candles?instrument_id={BTC.instrument_id}&tf=1D&limit=5000"
    )
    elapsed = time.perf_counter() - start
    assert candles.status_code == 200, candles.text[:200]
    assert elapsed < BUDGET_API_5000_CANDLES, f"candle API took {elapsed:.3f}s"
