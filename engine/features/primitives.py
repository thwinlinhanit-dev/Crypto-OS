"""Section 5 numeric primitives: pure functions over series (spec section 5).

Design note specs/design/m3_features.md sections 1-2. Everything here takes
plain sequences and returns plain sequences - no Polars, no I/O, no parameter
that is not passed in explicitly (PD-11). Every output at index ``i`` depends
only on inputs at or before ``i`` (PD-2), so cutting the input yields a
prefix of the output; properties in tests/test_features_truncation.py.

Warm-up rule (section 5 line 228 "null during warm-up"): a window of ``n``
values emits ``null`` until ``n`` values exist, the rule section 5 states for
``sma``. ``ema`` is the documented exception - its first output is ``x[0]``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import polars as pl

#: Bar columns a ``source`` parameter may name (section 5 ``highest(x, n)``).
BAR_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

Series = list[float | None]
_Relation = Callable[[float, float], bool]


def column(frame: pl.DataFrame, name: str) -> list[float]:
    """One bar column as floats, in bar order."""
    return [float(value) for value in frame[name].to_list()]


def rolling_mean(values: Sequence[float], length: int) -> Series:
    if not values:
        return []
    return pl.Series(list(values), dtype=pl.Float64).rolling_mean(
        window_size=length
    ).to_list()


def rolling_std_pop(values: Sequence[float], length: int) -> Series:
    """Population standard deviation (Bollinger, section 5)."""
    if not values:
        return []
    return pl.Series(list(values), dtype=pl.Float64).rolling_std(
        window_size=length, ddof=0
    ).to_list()


def ema(values: Sequence[float | None], length: int) -> Series:
    """``alpha = 2/(n+1)``; first output is ``x[0]`` (Pine ``ta.ema``, no seed).

    A ``null`` input emits ``null`` at that index and the recursion resumes
    from the last state at the next non-null input (Pine ``na`` semantics);
    with numeric input this is exactly section 5's definition.
    """
    alpha = 2.0 / (length + 1)
    out: Series = [None] * len(values)
    state: float | None = None
    for index, value in enumerate(values):
        if value is None:
            continue
        state = value if state is None else alpha * value + (1.0 - alpha) * state
        out[index] = state
    return out


def rma(values: Sequence[float], length: int) -> Series:
    """``alpha = 1/n``, seeded with ``sma(x, n)`` at index ``n-1`` (section 5)."""
    out: Series = [None] * len(values)
    if len(values) < length:
        return out
    state = sum(values[:length]) / length
    out[length - 1] = state
    for index in range(length, len(values)):
        state = state + (values[index] - state) / length
        out[index] = state
    return out


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    """``t=0`` is ``high-low``; afterwards the max of the three gaps (section 5)."""
    if not high:
        return []
    out = [high[0] - low[0]]
    for index in range(1, len(high)):
        out.append(
            max(
                high[index] - low[index],
                abs(high[index] - close[index - 1]),
                abs(low[index] - close[index - 1]),
            )
        )
    return out


def highest(values: Sequence[float], length: int) -> Series:
    """Max over the last ``n`` bars including the current one; null before."""
    out: Series = [None] * len(values)
    for index in range(length - 1, len(values)):
        out[index] = max(values[index - length + 1 : index + 1])
    return out


def lowest(values: Sequence[float], length: int) -> Series:
    """Min over the last ``n`` bars including the current one; null before."""
    out: Series = [None] * len(values)
    for index in range(length - 1, len(values)):
        out[index] = min(values[index - length + 1 : index + 1])
    return out


def barssince(condition: Sequence[bool]) -> list[int | None]:
    """Bars since ``condition`` was last true: ``0`` where true, null before.

    Boolean input, so it lives here rather than in the numeric registry
    (design D-015).
    """
    out: list[int | None] = [None] * len(condition)
    last: int | None = None
    for index, value in enumerate(condition):
        if value:
            last = index
        out[index] = None if last is None else index - last
    return out


def pivotlow(values: Sequence[float], left: int, right: int) -> Series:
    """Pivot low: strict on both sides, value emitted ``right`` bars later.

    A candidate at ``p`` needs ``values[p] < values[p-k]`` for every ``k`` in
    ``1..left`` and ``values[p] < values[p+k]`` for every ``k`` in
    ``1..right``; an equal neighbour disqualifies it (design D-016). The value
    is written at ``p + right`` - the bar where Pine's ``ta.pivotlow`` first
    knows it - and is therefore never visible to anything before that bar.
    """
    return _pivot(values, left, right, _is_lower)


def pivothigh(values: Sequence[float], left: int, right: int) -> Series:
    """Pivot high: the mirror of :func:`pivotlow` with strict ``>``."""
    return _pivot(values, left, right, _is_higher)


def _is_lower(center: float, other: float) -> bool:
    return center < other


def _is_higher(center: float, other: float) -> bool:
    return center > other


def _pivot(
    values: Sequence[float],
    left: int,
    right: int,
    relation: _Relation,
) -> Series:
    out: Series = [None] * len(values)
    for candidate in range(left, len(values) - right):
        center = values[candidate]
        if all(relation(center, values[candidate - k]) for k in range(1, left + 1)) and all(
            relation(center, values[candidate + k]) for k in range(1, right + 1)
        ):
            out[candidate + right] = center
    return out


def macd(
    values: Sequence[float | None], fast: int, slow: int, signal: int
) -> dict[str, Series]:
    """MACD(fast, slow, signal): line, signal line and histogram (section 5)."""
    line: Series = [
        None if short is None or long is None else short - long
        for short, long in zip(
            ema(values, fast), ema(values, slow), strict=True
        )
    ]
    signal_line = ema(line, signal)
    return {
        "macd": line,
        "signal": signal_line,
        "histogram": [
            None if value is None or base is None else value - base
            for value, base in zip(line, signal_line, strict=True)
        ],
    }


def adx(
    high: Sequence[float], low: Sequence[float], close: Sequence[float], length: int
) -> Series:
    """Wilder ADX: DI from ``rma``-smoothed TR and directional movement.

    A zero denominator (flat bars) yields ``null``, never ``inf`` - the same
    edge handling ``rvol`` and ``rsi`` use (M2 interpretation notes). The
    DX run starts at the first bar where both DIs exist and stops at the first
    gap; ``rma`` over a run shorter than ``length`` stays ``null``.
    """
    size = len(close)
    out: Series = [None] * size
    if size == 0:
        return out
    true_ranges = true_range(high, low, close)
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for index in range(1, size):
        rise = high[index] - high[index - 1]
        fall = low[index - 1] - low[index]
        plus_dm[index] = rise if rise > fall and rise > 0.0 else 0.0
        minus_dm[index] = fall if fall > rise and fall > 0.0 else 0.0

    smoothed_tr = rma(true_ranges, length)
    smoothed_plus = rma(plus_dm, length)
    smoothed_minus = rma(minus_dm, length)

    dx: Series = [None] * size
    for index in range(size):
        denominator = smoothed_tr[index]
        if denominator is None or denominator == 0.0:
            continue
        plus = smoothed_plus[index]
        minus = smoothed_minus[index]
        if plus is None or minus is None:
            continue
        di_plus = 100.0 * plus / denominator
        di_minus = 100.0 * minus / denominator
        total = di_plus + di_minus
        dx[index] = None if total == 0.0 else 100.0 * abs(di_plus - di_minus) / total

    start = next((index for index, value in enumerate(dx) if value is not None), None)
    if start is None:
        return out
    run: list[float] = []
    for value in dx[start:]:
        if value is None:
            break
        run.append(value)
    for offset, value in enumerate(rma(run, length)):
        out[start + offset] = value
    return out


def atr_percentile(values: Sequence[float | None], window: int) -> Series:
    """Percentile rank of each value inside the trailing ``window`` bars.

    ``100 * (less + 0.5 * equal) / window``: a tied window sits at 50, which
    keeps a flat series out of both tails. A window containing ``null``
    (warm-up) emits ``null``.
    """
    out: Series = [None] * len(values)
    for index in range(window - 1, len(values)):
        sample = list(values[index - window + 1 : index + 1])
        if any(value is None for value in sample):
            continue
        current = values[index]
        if current is None:
            continue
        numbers = [float(value) for value in sample if value is not None]
        less = sum(1 for value in numbers if value < current)
        equal = sum(1 for value in numbers if value == current)
        out[index] = 100.0 * (less + 0.5 * equal) / window
    return out
