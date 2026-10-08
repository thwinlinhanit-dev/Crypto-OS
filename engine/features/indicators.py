"""Chart indicators behind the ``/v1/indicators`` contract (spec section 5).

Each indicator is a pure function over the canonical bar columns; warm-up
values are ``null`` and every output at bar ``t`` depends only on inputs at or
before ``t`` (PD-2). Parameter resolution happens in :func:`resolve_indicator`
through ``config/defaults.yaml`` (PD-11): a ``null`` default documents a
*required* parameter, a numeric default documents the value section 5 pins.

The numeric machinery lives in :mod:`engine.features.primitives` so detectors
(M4) and the parity tool (M3) share exactly one implementation of each
definition; this module owns only the registry contract.

INTERPRETATIONS (docs/DECISIONS.md OQ-8): ``rsi`` emits its first value at
index ``n`` (the first ``n`` price changes live at indices ``1..n``);
``avg_loss == 0`` with ``avg_gain > 0`` is ``100.0``, both zero is ``null``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from engine.config.defaults import load_defaults, resolve_params
from engine.errors import ValidationError
from engine.features.primitives import (
    BAR_COLUMNS,
    adx,
    column,
    ema,
    highest,
    lowest,
    macd,
    pivothigh,
    pivotlow,
    rma,
    rolling_mean,
    rolling_std_pop,
    true_range,
)

DAY_MS = 86_400_000

Values = dict[str, list[float | None]]
ComputeFn = Callable[[pl.DataFrame, Mapping[str, Any]], Values]



def _compute_sma(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    return {"sma": rolling_mean(column(frame, "close"), int(params["length"]))}


def _compute_ema(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    return {"ema": ema(column(frame, "close"), int(params["length"]))}


def _compute_atr(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    ranges = true_range(column(frame, "high"), column(frame, "low"), column(frame, "close"))
    return {"atr": rma(ranges, int(params["length"]))}


def _compute_rsi(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    length = int(params["length"])
    close = column(frame, "close")
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
    close = column(frame, "close")
    middle = rolling_mean(close, length)
    deviation = rolling_std_pop(close, length)
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
    volume = column(frame, "volume")
    sma = rolling_mean(volume, length)
    lagged: list[float | None] = [None, *sma[:-1]]
    return {
        "rvol": [
            value / previous if previous not in (None, 0.0) else None
            for value, previous in zip(volume, lagged, strict=True)
        ]
    }


def _compute_cpos(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    high = column(frame, "high")
    low = column(frame, "low")
    close = column(frame, "close")
    return {
        "cpos": [
            (c - lo) / (hi - lo) if hi - lo > 0.0 else 0.5
            for hi, lo, c in zip(high, low, close, strict=True)
        ]
    }


def _compute_upper_wick(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    high = column(frame, "high")
    low = column(frame, "low")
    open_ = column(frame, "open")
    close = column(frame, "close")
    return {
        "upper_wick": [
            (hi - max(o, c)) / (hi - lo) if hi - lo > 0.0 else 0.0
            for hi, lo, o, c in zip(high, low, open_, close, strict=True)
        ]
    }


def _compute_vwap(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    # Section 5: UTC-day anchor - cumulative typical-price volume per UTC day.
    open_time = [int(value) for value in frame["open_time"].to_list()]
    high = column(frame, "high")
    low = column(frame, "low")
    close = column(frame, "close")
    volume = column(frame, "volume")
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


def _compute_rma(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    return {"rma": rma(column(frame, "close"), int(params["length"]))}


def _compute_tr(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    out: list[float | None] = []
    out.extend(true_range(column(frame, "high"), column(frame, "low"), column(frame, "close")))
    return {"tr": out}


def _source(frame: pl.DataFrame, params: Mapping[str, Any]) -> list[float]:
    return column(frame, str(params["source"]))


def _compute_highest(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    return {"highest": highest(_source(frame, params), int(params["length"]))}


def _compute_lowest(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    return {"lowest": lowest(_source(frame, params), int(params["length"]))}


def _compute_donchian_hi(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    return {"donchian_hi": highest(column(frame, "high"), int(params["length"]))}


def _compute_donchian_lo(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    return {"donchian_lo": lowest(column(frame, "low"), int(params["length"]))}


def _compute_pivotlow(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    values = pivotlow(
        _source(frame, params), int(params["left"]), int(params["right"])
    )
    return {"pivotlow": values}


def _compute_pivothigh(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    values = pivothigh(
        _source(frame, params), int(params["left"]), int(params["right"])
    )
    return {"pivothigh": values}


def _compute_macd(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    return macd(
        column(frame, "close"),
        int(params["fast"]),
        int(params["slow"]),
        int(params["signal"]),
    )


def _compute_adx(frame: pl.DataFrame, params: Mapping[str, Any]) -> Values:
    values = adx(
        column(frame, "high"),
        column(frame, "low"),
        column(frame, "close"),
        int(params["length"]),
    )
    return {"adx": values}


@dataclass(frozen=True)
class IndicatorSpec:
    """One registry entry: outputs plus the pure compute function.

    ``categorical`` lists parameters restricted to a fixed vocabulary (the bar
    ``source`` names of section 5) so a typo is a 422 instead of a runtime
    ``KeyError`` on the frame.
    """

    name: str
    outputs: tuple[str, ...]
    positive_int_params: tuple[str, ...]
    compute: ComputeFn
    categorical: dict[str, tuple[str, ...]] = field(default_factory=dict)


_SOURCE = {"source": BAR_COLUMNS}
"""``high``/``low``/``close``/``open``/``volume`` (section 5)."""


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
    # M3: the rest of the section 5 set (design note specs/design/m3_features.md).
    "rma": IndicatorSpec("rma", ("rma",), ("length",), _compute_rma),
    "tr": IndicatorSpec("tr", ("tr",), (), _compute_tr),
    "highest": IndicatorSpec(
        "highest", ("highest",), ("length",), _compute_highest, dict(_SOURCE)
    ),
    "lowest": IndicatorSpec(
        "lowest", ("lowest",), ("length",), _compute_lowest, dict(_SOURCE)
    ),
    "donchian_hi": IndicatorSpec("donchian_hi", ("donchian_hi",), ("length",), _compute_donchian_hi),
    "donchian_lo": IndicatorSpec("donchian_lo", ("donchian_lo",), ("length",), _compute_donchian_lo),
    "pivotlow": IndicatorSpec(
        "pivotlow", ("pivotlow",), ("left", "right"), _compute_pivotlow, dict(_SOURCE)
    ),
    "pivothigh": IndicatorSpec(
        "pivothigh", ("pivothigh",), ("left", "right"), _compute_pivothigh, dict(_SOURCE)
    ),
    "macd": IndicatorSpec(
        "macd", ("macd", "signal", "histogram"), ("fast", "slow", "signal"), _compute_macd
    ),
    "adx": IndicatorSpec("adx", ("adx",), ("length",), _compute_adx),
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
    for key, allowed in spec.categorical.items():
        if resolved.get(key) not in allowed:
            raise ValidationError(
                f"parameter '{key}' of '{name}' must be one of {', '.join(allowed)}",
                detail={"param": key, "value": resolved.get(key), "allowed": list(allowed)},
            )
    if name == "macd" and int(resolved["slow"]) <= int(resolved["fast"]):
        raise ValidationError(
            "macd requires slow > fast",
            detail={"param": "slow", "value": resolved["slow"], "fast": resolved["fast"]},
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
