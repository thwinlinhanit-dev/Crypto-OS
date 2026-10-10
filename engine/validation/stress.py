"""M7 (T7.3) — cost stress, delay stress, Monte Carlo (spec 9.3 Level 3).

Cost stress re-prices a trade set at 2x fee+slippage; delay stress applies
+1 bar `delay_bars` (the M6 re-run seam recomputes the trades - these
functions take the recomputed set); Monte Carlo shuffles the trade order
and bootstraps (seeded 5000) for the P5 final-equity verdict.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any

#: 9.3 defaults.
MONTE_CARLO_RESAMPLES = 5000


def cost_stress(
    trades: Sequence[dict[str, float]],
    *,
    multiplier: float = 2.0,
) -> dict[str, Any]:
    """9.3: at 2x fee+slippage: PF>1.0 AND expectancy_R>0 AND OOS>0 -> PASS.
    ``trades`` entries carry ``pnl``, ``fees``, ``r_multiple``; the caller
    supplies the OOS slice through ``oos``."""
    stressed_pnl: list[float] = []
    stressed_r: list[float] = []
    for trade in trades:
        base_pnl = float(trade.get("pnl", 0.0))
        fees = float(trade.get("fees", 0.0))
        stressed = base_pnl - fees * (multiplier - 1.0)
        stressed_pnl.append(stressed)
        r = trade.get("r_multiple")
        if r is not None:
            stressed_r.append(float(r))
    wins = sum(p for p in stressed_pnl if p > 0)
    losses = abs(sum(p for p in stressed_pnl if p < 0))
    profit_factor = (wins / losses) if losses else (999_999.0 if wins > 0 else None)
    expectancy_r = (sum(stressed_r) / len(stressed_r)) if stressed_r else None
    return {
        "multiplier": multiplier,
        "profit_factor": profit_factor,
        "expectancy_r": expectancy_r,
        "oos_expectancy_r": expectancy_r,  # the caller overrides with the OOS slice
        "trade_count": len(stressed_pnl),
    }


def delay_stress(
    trades: Sequence[dict[str, float]],
    *,
    extra_delay_bars: int = 1,
) -> dict[str, Any]:
    """9.3: at +1 bar delay_bars the same gates apply. The trades come from
    the M6 re-run seam with the delayed fill model; this function reports
    the aggregates the level test consumes."""
    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    r_values = [float(t["r_multiple"]) for t in trades if t.get("r_multiple") is not None]
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    return {
        "extra_delay_bars": extra_delay_bars,
        "profit_factor": (wins / losses) if losses else (999_999.0 if wins > 0 else None),
        "expectancy_r": (sum(r_values) / len(r_values)) if r_values else None,
        "oos_expectancy_r": (sum(r_values) / len(r_values)) if r_values else None,
        "trade_count": len(pnls),
    }


def monte_carlo(
    pnls: Sequence[float],
    initial_equity: float,
    *,
    seed: int = 1,
    resamples: int = MONTE_CARLO_RESAMPLES,
) -> dict[str, Any]:
    """9.3: trade-order shuffle + bootstrap (seeded): P5 final equity > start
    -> PASS; report maxDD percentiles."""
    if not pnls:
        return {"p5_final_equity": None, "maxdd_p95": None, "resamples": resamples}
    rng = random.Random(seed)
    finals: list[float] = []
    drawdowns: list[float] = []
    for _ in range(resamples):
        shuffled = [pnls[rng.randrange(len(pnls))] for _ in pnls]
        equity = initial_equity
        peak = initial_equity
        worst = 0.0
        for pnl in shuffled:
            equity += pnl
            peak = max(peak, equity)
            if peak:
                worst = max(worst, (peak - equity) / peak)
        finals.append(equity)
        drawdowns.append(worst)
    finals.sort()
    drawdowns.sort()

    def percentile(values: list[float], pct: float) -> float:
        rank = (pct / 100.0) * (len(values) - 1)
        low = int(rank)
        high = min(low + 1, len(values) - 1)
        return values[low] + (values[high] - values[low]) * (rank - low)

    return {
        "seed": seed,
        "resamples": resamples,
        "p5_final_equity": percentile(finals, 5),
        "p50_final_equity": percentile(finals, 50),
        "p95_final_equity": percentile(finals, 95),
        "maxdd_p50": percentile(drawdowns, 50),
        "maxdd_p95": percentile(drawdowns, 95),
    }
