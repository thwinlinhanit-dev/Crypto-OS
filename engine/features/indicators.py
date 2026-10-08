"""M2 chart indicators (spec section 5) behind the ``/v1/indicators`` contract.

Each indicator is a pure function over the canonical bar columns; warm-up
values are ``null`` and every output at bar ``t`` depends only on inputs at or
before ``t`` (PD-2). Parameter resolution happens in :func:`resolve_indicator`
through ``config/defaults.yaml`` (PD-11): a ``null`` default documents a
*required* parameter, a numeric default documents the value section 5 pins.

INTERPRETATIONS (docs/DECISIONS.md OQ-8): ``rsi`` emits its first value at
index ``n`` (the first ``n`` price changes live at indices ``1..n``);
``avg_loss == 0`` with ``avg_gain > 0`` is ``100.0``, both zero is ``null``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import polars as pl

from engine.config.defaults import load_defaults, resolve_params
from engine.errors import ValidationError

DAY_MS = 86_400_000

Values = dict[str, list[float | None]]
ComputeFn = Callable[[pl.DataFrame, Mapping[str, Any]], Values]


def _column(frame: pl.DataFrame, name: str) -> list[float]:
    return [float(value) for value in frame[name].to_list()]


def _rolling_mean(values: list[float], length: int) -> list[float | None]:
    if not values:
        return []
    return pl.Series(values, dtype=pl.Float64).rolling_mean(
        window_size=length
    ).to_list()


def _rolling_std_pop(values: list[float], length: int) -> list[float | None]:
    if not values:
        return []
    return pl.Series(values, dtype=pl.Float64).rolling_std(
        window_size=length, ddof=0
    ).to_list()


def _ema(values: list[float], length: int) -> list[float | None]:
    # Section 5: alpha = 2/(n+1), first output = x[0] (Pine ta.ema, no seed).
    if not values:
        return []
    alpha = 2.0 / (length + 1)
    out: list[float | None] = [values[0]]
    previous = values[0]
    for value in values[1:]:
        previous = alpha * value + (1.0 - alpha) * previous
        out.append(previous)
    return out


def _rma(values: list[float], length: int) -> list[float | None]:
    # Section 5: alpha = 1/n, seeded with sma(x, n) at index n-1, null before.
    out: list[float | None] = [None] * len(values)
    if len(values) < length:
        return out
    previous = sum(values[:length]) / length
    out[length - 1] = previous
    for index in range(length, len(values)):
        previous = previous + (values[index] - previous) / length
        out[index] = previous
    return out


def _true_range(frame: pl.DataFrame) -> list[float]:
    high = _column(frame, "high")
    low = _column(frame, "low")
    close = _column(frame, "close")
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


def _compute_sma(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    return {"sma": _rolling_mean(_column(frame, "close"), int(params["length"]))}


def _compute_ema(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    return {"ema": _ema(_column(frame, "close"), int(params["length"]))}


def _compute_atr(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    return {"atr": _rma(_true_range(frame), int(params["length"]))}


def _compute_rsi(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    length = int(params["length"])
    close = _column(frame, "close")
    out: list[float | None] = [None] * len(close)
    if len(close) < length + 1:
        return {"rsi": out}
    gains = [0.0] * len(close)
    losses = [0.0] * len(close)
    for index in range(1, len(close)):
        change = close[index] - close[index - 1]
        gains[index] = max(change, 0.0)
        losses[index] = max(-change, 0.0)
    avg_gain = sum(gains[1 : length + 1]) / length
    avg_loss = sum(losses[1 : length + 1]) / length

    def ratio(gain: float, loss: float) -> float | None:
        if loss == 0.0:
            return 100.0 if gain > 0.0 else None  # rs -> inf ; 0/0 -> na
        return 100.0 - 100.0 / (1.0 + gain / loss)

    out[length] = ratio(avg_gain, avg_loss)
    for index in range(length + 1, len(close)):
        avg_gain += (gains[index] - avg_gain) / length
        avg_loss += (losses[index] - avg_loss) / length
        out[index] = ratio(avg_gain, avg_loss)
    return {"rsi": out}


def _compute_bb(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    length = int(params["length"])
    mult = float(params["mult"])
    close = _column(frame, "close")
    middle = _rolling_mean(close, length)
    deviation = _rolling_std_pop(close, length)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for mid, sd in zip(middle, deviation, strict=True):
        if mid is None or sd is None:
            upper.append(None)
            lower.append(None)
        else:
            upper.append(mid + mult * sd)
            lower.append(mid - mult * sd)
    return {"upper": upper, "middle": middle, "lower": lower}


def _compute_rvol(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    length = int(params["length"])
    volume = _column(frame, "volume")
    sma = _rolling_mean(volume, length)
    lagged: list[float | None] = [None, *sma[:-1]]
    return {
        "rvol": [
            value / previous if previous not in (None, 0.0) else None
            for value, previous in zip(volume, lagged, strict=True)
        ]
    }


def _compute_cpos(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    high = _column(frame, "high")
    low = _column(frame, "low")
    close = _column(frame, "close")
    return {
        "cpos": [
            (c - lo) / (hi - lo) if hi - lo > 0.0 else 0.5
            for hi, lo, c in zip(high, low, close, strict=True)
        ]
    }


def _compute_upper_wick(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    high = _column(frame, "high")
    low = _column(frame, "low")
    open_ = _column(frame, "open")
    close = _column(frame, "close")
    return {
        "upper_wick": [
            (hi - max(o, c)) / (hi - lo) if hi - lo > 0.0 else 0.0
            for hi, lo, o, c in zip(high, low, open_, close, strict=True)
        ]
    }


def _compute_vwap(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    # Section 5: UTC-day anchor - cumulative typical-price volume per UTC day.
    open_time = [int(value) for value in frame["open_time"].to_list()]
    high = _column(frame, "high")
    low = _column(frame, "low")
    close = _column(frame, "close")
    volume = _column(frame, "volume")
    cumulative_pv: dict[int, float] = {}
    cumulative_volume: dict[int, float] = {}
    out: list[float | None] = []
    for index, moment in enumerate(open_time):
        day = moment // DAY_MS
        typical = (high[index] + low[index] + close[index]) / 3.0
        cumulative_pv[day] = cumulative_pv.get(day, 0.0) + typical * volume[index]
        cumulative_volume[day] = cumulative_volume.get(day, 0.0) + volume[index]
        total = cumulative_volume[day]
        out.append(cumulative_pv[day] / total if total > 0.0 else None)
    return {"vwap": out}


@dataclass(frozen=True)
class IndicatorSpec:
    """One registry entry: outputs plus the pure compute function."""

    name: str
    outputs: tuple[str, ...]
    positive_int_params: tuple[str, ...]
    compute: ComputeFn


REGISTRY: dict[str, IndicatorSpec] = {
    "sma": IndicatorSpec("sma", ("sma",), ("length",), _compute_sma),
    "ema": IndicatorSpec("ema", ("ema",), ("length",), _compute_ema),
    "atr": IndicatorSpec("atr", ("atr",), ("length",), _compute_atr),
    "rsi": IndicatorSpec("rsi", ("rsi",), ("length",), _compute_rsi),
    "bb": IndicatorSpec("bb", ("upper", "middle", "lower"), ("length",), _compute_bb),
    "rvol": IndicatorSpec("rvol", ("rvol",), ("length",), _compute_rvol),
    "cpos": IndicatorSpec("cpos", ("cpos",), (), _compute_cpos),
    "upper_wick": IndicatorSpec("upper_wick", ("upper_wick",), (), _compute_upper_wick),
    "vwap": IndicatorSpec("vwap", ("vwap",), (), _compute_vwap),
}


def known_indicators() -> tuple[str, ...]:
    """Registry names, sorted (deterministic order for menus and tests)."""
    return tuple(sorted(REGISTRY))


def resolve_indicator(
    name: str,
    params: Mapping[str, Any] | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> tuple[IndicatorSpec, dict[str, Any]]:
    """Validate ``name`` and resolve its parameters (PD-11, section 5).

    Raises ``ValidationError`` for unknown names and domain violations and
    ``ParamError`` (the 422 vocabulary) for undocumented/missing parameters.
    """
    spec = REGISTRY.get(name)
    if spec is None:
        raise ValidationError(
            f"unknown indicator '{name}'",
            detail={"name": name, "known": list(known_indicators())},
        )
    document = dict(defaults) if defaults is not None else load_defaults()
    resolved = resolve_params(document, f"indicators.{name}", params)
    for key in spec.positive_int_params:
        value = resolved.get(key)
        if type(value) is not int or value < 1:
            raise ValidationError(
                f"parameter '{key}' of '{name}' must be an integer >= 1",
                detail={"param": key, "value": value},
            )
    return spec, resolved


def compute_indicator(
    frame: pl.DataFrame,
    *,
    name: str,
    params: Mapping[str, Any] | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve then compute; returns ``{"t": open_times, "series": [...]}``."""
    spec, resolved = resolve_indicator(name, params, defaults)
    if frame.is_empty():
        values: Values = {key: [] for key in spec.outputs}
    else:
        values = spec.compute(frame, resolved)
    return {
        "t": frame["open_time"].to_list() if frame.height else [],
        "series": [{"key": key, "values": values[key]} for key in spec.outputs],
    }
