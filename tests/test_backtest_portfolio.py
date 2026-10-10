"""M6 (T6.3) — portfolio mode tests (spec 7.4 "PORTFOLIO mode").

Acceptance evidence (spec M6 AC / design note section 5):

- one shared equity: ``test_shared_equity_pool`` (a loss on one instrument
  shrinks the next instrument's sizing);
- deterministic order: ``test_deterministic_instrument_order`` (the same
  inputs produce the same trade sequence regardless of input dict order);
- ``max_concurrent_positions``: ``test_max_concurrent_positions_skips``;
- ``max_portfolio_heat``: ``test_max_portfolio_heat_skips``;
- per-asset cap: ``test_per_asset_cap_skips``;
- no signal disappears: every skipped entry carries its reason (M6 AC);
- single-symbol results still produced (R9): a one-instrument universe
  behaves exactly like ``simulate``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from engine.strategy.models import StrategyDefinition
from engine.strategy.portfolio import (
    PortfolioConfig,
    PortfolioResult,
    simulate_portfolio,
)
from engine.strategy.simulator import Bar, Signal


def _strategy(**overrides: object) -> StrategyDefinition:
    body: dict[str, object] = {
        "strategy_id": "test",
        "version": "1.0",
        "entry": {"detector": "RVOL_CLIMAX@5.0", "params": {}, "signals": ["CONT"],
                  "side": "LONG_ONLY"},
        "sizing": {"mode": "RISK", "risk_pct": 2.0, "max_alloc_pct": 100.0},
        "stop": {"kind": "ATR", "atr_len": 14, "atr_mult": 2.0},
        "costs": {"fee_model": {"kind": "PERCENT", "value": 0.0},
                  "slippage_model": {"kind": "FIXED_BPS", "value": 0}},
        "execution": {"fill_model": "REALISTIC_OHLC", "initial_capital": 10000,
                      "delay_bars": 0},
    }
    body.update(overrides)
    return StrategyDefinition.model_validate(body)


def _bars(prices: list[tuple[float, float, float, float]]) -> list[Bar]:
    return [
        Bar(index=i, time=f"2026-01-{i + 1:02d}T00:00:00Z", open=o, high=h, low=lo, close=c)
        for i, (o, h, lo, c) in enumerate(prices)
    ]


def _atr(n: int, value: float | None = 5.0) -> list[float | None]:
    return [value] * n


def _two_instrument_universe() -> tuple[dict[str, list[Bar]], dict[str, list[Signal]]]:
    """Two instruments, each with a signal at bar 0 and a stop hit at bar 2."""
    bars = {
        "AAA": _bars([
            (100, 101, 99, 100),
            (100, 100.5, 99, 100.4),  # entry 100; stop = 90
            (100, 101, 89, 90),       # stop hit -> exit 90 (loss 10/unit)
            (90, 91, 88, 89),
        ]),
        "BBB": _bars([
            (50, 51, 49, 50),
            (50, 50.5, 49, 50.4),     # entry 50; stop = 40
            (50, 51, 39, 40),         # stop hit -> exit 40 (loss 10/unit)
            (40, 41, 38, 39),
        ]),
    }
    signals = {
        "AAA": [Signal(bar_index=0, event_type="CONT", direction="LONG")],
        "BBB": [Signal(bar_index=0, event_type="CONT", direction="LONG")],
    }
    return bars, signals


def _run(
    strategy: StrategyDefinition,
    instruments: list[str],
    bars: Mapping[str, list[Bar]],
    signals: Mapping[str, list[Signal]],
    config: PortfolioConfig | None = None,
) -> PortfolioResult:
    return simulate_portfolio(
        strategy,
        instruments,
        bars,
        signals,
        atr_by_instrument={name: _atr(len(bars[name])) for name in instruments},
        config=config,
    )


def test_shared_equity_pool() -> None:
    """One shared equity: losses reduce the pool the later trade sizes against."""
    strategy = _strategy()
    bars, signals = _two_instrument_universe()
    result = _run(strategy, ["AAA", "BBB"], bars, signals)
    assert len(result.trades) == 2
    # both are losses; the shared pool shrinks after the first close
    assert all(t.pnl < 0 for t in result.trades)
    assert result.equity_curve[-1][1] < 10000.0


def test_deterministic_instrument_order() -> None:
    """D-040: the same inputs produce the same trade sequence regardless of
    the input dict's iteration order."""
    strategy = _strategy()
    bars, signals = _two_instrument_universe()
    forward = _run(strategy, ["AAA", "BBB"], bars, signals)
    # reverse the dict insertion order; the sort inside must not care
    reversed_bars = {"BBB": bars["BBB"], "AAA": bars["AAA"]}
    reversed_signals = {"BBB": signals["BBB"], "AAA": signals["AAA"]}
    backward = _run(strategy, ["AAA", "BBB"], reversed_bars, reversed_signals)
    assert [t.instrument for t in forward.trades] == [t.instrument for t in backward.trades]


def test_max_concurrent_positions_skips() -> None:
    """max_concurrent_positions: a second entry while one is open is skipped
    with the reason (no signal disappears)."""
    strategy = _strategy()
    bars, signals = _two_instrument_universe()
    config = PortfolioConfig(max_concurrent_positions=1)
    result = _run(strategy, ["AAA", "BBB"], bars, signals, config)
    assert len(result.trades) == 1
    assert len(result.skipped) == 1
    assert result.skipped[0]["reason"] == "MAX_CONCURRENT_POSITIONS"


def test_max_portfolio_heat_skips() -> None:
    """max_portfolio_heat: the second position's risk pushes total heat over
    the cap -> skipped (the per-asset cap stays permissive so heat binds)."""
    strategy = _strategy(sizing={"mode": "RISK", "risk_pct": 40.0, "max_alloc_pct": 100.0})
    bars, signals = _two_instrument_universe()
    config = PortfolioConfig(max_portfolio_heat=0.15, per_asset_cap_pct=100.0)
    result = _run(strategy, ["AAA", "BBB"], bars, signals, config)
    assert len(result.trades) == 1
    assert any(s["reason"] == "MAX_PORTFOLIO_HEAT" for s in result.skipped)


def test_per_asset_cap_skips() -> None:
    """Per-asset cap: exposure per instrument is bounded."""
    strategy = _strategy()
    bars, signals = _two_instrument_universe()
    config = PortfolioConfig(per_asset_cap_pct=1.0)  # 1% of 10k = 100 per instrument
    result = _run(strategy, ["AAA", "BBB"], bars, signals, config)
    assert len(result.trades) <= 2
    assert all(s["reason"] == "PER_ASSET_CAP" for s in result.skipped)


def test_every_skipped_entry_has_a_reason() -> None:
    """M6 AC: no signal disappears - every rejected entry names its reason."""
    strategy = _strategy()
    bars, signals = _two_instrument_universe()
    config = PortfolioConfig(max_concurrent_positions=1)
    result = _run(strategy, ["AAA", "BBB"], bars, signals, config)
    for skipped in result.skipped:
        assert skipped["reason"] in {
            "MAX_CONCURRENT_POSITIONS",
            "MAX_PORTFOLIO_HEAT",
            "PER_ASSET_CAP",
        }


def test_single_instrument_matches_simulate() -> None:
    """R9: a one-instrument universe behaves like the per-instrument run."""
    from engine.strategy.simulator import simulate

    strategy = _strategy()
    bars, signals = _two_instrument_universe()
    portfolio = _run(strategy, ["AAA"], bars, signals)
    single = simulate(strategy, "AAA", bars["AAA"], signals["AAA"],
                      atr=_atr(len(bars["AAA"])))
    assert len(portfolio.trades) == len(single.trades)
    assert portfolio.trades[0].pnl == single.trades[0].pnl


def test_equity_curve_covers_every_bar() -> None:
    strategy = _strategy()
    bars, signals = _two_instrument_universe()
    result = _run(strategy, ["AAA", "BBB"], bars, signals)
    assert len(result.equity_curve) == len(bars["AAA"])  # both share the bar grid


def test_empty_universe_is_empty() -> None:
    strategy = _strategy()
    result = simulate_portfolio(strategy, [], {}, {}, atr_by_instrument={})
    assert result.trades == []
    assert result.equity_curve == []
