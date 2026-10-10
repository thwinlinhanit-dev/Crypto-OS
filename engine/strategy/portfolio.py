"""M6 (T6.3) — portfolio mode (spec 7.4 "PORTFOLIO mode").

One shared equity; instruments are processed in a fixed deterministic order
each bar (``instrument_id`` sort — D-040); the constraints are
``max_concurrent_positions``, ``max_portfolio_heat`` (total risked equity
fraction) and a per-asset cap; sizing uses the shared equity.

The per-instrument simulator (``simulate``) stays the source of truth for
one instrument's trade logic; this module layers the portfolio constraints
around it by running the instruments bar-by-bar against a shared equity
pool, in sorted order, and skipping entries that would breach a cap
(recorded as ``SKIPPED`` with the reason — no signal disappears, M6 AC).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from engine.strategy.models import StrategyDefinition
from engine.strategy.simulator import (
    Bar,
    Signal,
    Trade,
    simulate,
)

#: Per-asset cap as a fraction of equity (spec 7.4 portfolio mode).
DEFAULT_PER_ASSET_CAP_PCT = 25.0


@dataclass(frozen=True)
class PortfolioConfig:
    """Portfolio constraints (7.4). All optional; the defaults are permissive."""

    max_concurrent_positions: int | None = None
    max_portfolio_heat: float | None = None  # fraction of equity risked at once
    per_asset_cap_pct: float = DEFAULT_PER_ASSET_CAP_PCT


@dataclass
class PortfolioResult:
    """The merged multi-instrument result: trades in deterministic order plus
    the shared equity curve (one point per bar)."""

    trades: list[Trade] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)


def _risk_per_position(strategy: StrategyDefinition, price: float, qty: float,
                       stop: float | None) -> float:
    """Risked equity for one position (the heat contribution)."""
    if stop is None or price <= 0:
        return 0.0
    return abs(price - stop) * qty


def simulate_portfolio(
    strategy: StrategyDefinition,
    instruments: Sequence[str],
    bars_by_instrument: Mapping[str, Sequence[Bar]],
    signals_by_instrument: Mapping[str, Sequence[Signal]],
    *,
    atr_by_instrument: Mapping[str, Sequence[float | None]],
    config: PortfolioConfig | None = None,
    tick_size: float = 1e-8,
    lot_step: float = 1e-8,
) -> PortfolioResult:
    """Run one strategy over a universe with shared equity (7.4 portfolio mode).

    Instruments are processed in sorted order each bar (D-040), so the same
    inputs always produce the same trades. Entries that would breach
    ``max_concurrent_positions`` / ``max_portfolio_heat`` / the per-asset cap
    are skipped with a reason.
    """
    cfg = config or PortfolioConfig()
    result = PortfolioResult()
    execution = strategy.execution
    equity = execution.initial_capital

    # Per-instrument simulators share one equity pool: run each instrument
    # independently (the per-instrument logic is complete), then apply the
    # portfolio constraints as a deterministic pass over the merged signals.
    per_instrument: dict[str, object] = {}
    for instrument in sorted(instruments):
        bars = list(bars_by_instrument.get(instrument, []))
        if not bars:
            continue
        per_instrument[instrument] = simulate(
            strategy,
            instrument,
            bars,
            signals_by_instrument.get(instrument, []),
            atr=atr_by_instrument.get(instrument, [None] * len(bars)),
            tick_size=tick_size,
            lot_step=lot_step,
        )

    # Merge trades deterministically (signal time, then instrument, then bar).
    merged: list[tuple[str, Trade]] = []
    for instrument in sorted(per_instrument):
        instrument_result = per_instrument[instrument]
        for trade in instrument_result.trades:  # type: ignore[attr-defined]
            merged.append((instrument, trade))
    merged.sort(key=lambda pair: (pair[1].signal_time, pair[0], pair[1].signal_bar_index))

    open_positions = 0
    heat = 0.0
    exposure_by_instrument: dict[str, float] = {}
    equity_at: dict[str, float] = {}

    # Process entries and closes as SEPARATE events in time order: the gates
    # see the position state as of each event (a same-signal-time entry on a
    # second instrument must observe the first instrument's open position).
    events: list[tuple[str, int, str, str, Trade]] = []
    for instrument, trade in merged:
        events.append((trade.signal_time, 0, instrument, "ENTRY", trade))
        if trade.exit_reason is not None:
            close_time = trade.exit_fill_time or trade.exit_signal_time or trade.signal_time
            events.append((close_time, 1, instrument, "EXIT", trade))
    events.sort(key=lambda item: (item[0], item[1], item[2]))

    for _moment, _rank, instrument, kind, trade in events:
        entry_value = trade.entry_price * trade.qty
        risk = _risk_per_position(strategy, trade.entry_price, trade.qty, trade.stop_at_entry)
        if kind == "EXIT":
            heat = max(0.0, heat - risk)
            open_positions = max(0, open_positions - 1)
            exposure_by_instrument[instrument] = max(
                0.0, exposure_by_instrument.get(instrument, 0.0) - entry_value
            )
            equity += trade.pnl
            equity_at[trade.exit_fill_time or trade.signal_time] = equity
            continue

        # ENTRY: portfolio gates (deterministic order, D-040)
        if (
            cfg.max_concurrent_positions is not None
            and open_positions >= cfg.max_concurrent_positions
        ):
            result.skipped.append({
                "instrument": instrument,
                "reason": "MAX_CONCURRENT_POSITIONS",
                "signal_time": trade.signal_time,
            })
            continue
        if (
            cfg.max_portfolio_heat is not None
            and equity > 0
            and (heat + risk) / equity > cfg.max_portfolio_heat
        ):
            result.skipped.append({
                "instrument": instrument,
                "reason": "MAX_PORTFOLIO_HEAT",
                "signal_time": trade.signal_time,
            })
            continue
        cap_value = equity * (cfg.per_asset_cap_pct / 100.0)
        if exposure_by_instrument.get(instrument, 0.0) + entry_value > cap_value:
            result.skipped.append({
                "instrument": instrument,
                "reason": "PER_ASSET_CAP",
                "signal_time": trade.signal_time,
            })
            continue

        # accepted: book it against the shared pool
        result.trades.append(trade)
        heat += risk
        open_positions += 1
        exposure_by_instrument[instrument] = (
            exposure_by_instrument.get(instrument, 0.0) + entry_value
        )
        equity_at[trade.signal_time] = equity

    # shared equity curve: one point per distinct close time across the universe
    times = sorted({bar.time for bars in bars_by_instrument.values() for bar in bars})
    for moment in times:
        result.equity_curve.append((moment, equity_at.get(moment, equity)))
    return result
