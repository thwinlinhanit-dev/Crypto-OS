"""Section 5 regime tags: descriptive labels, never filters (spec section 5).

Design note specs/design/m3_features.md section 2, decisions D-015..D-017.

Three tags - ``trend_state``, ``vol_state``, ``volume_state`` - each a pure
function over the bar frame returning ``list[str | None]``. They cannot ride
the registry's float-series contract (D-011), so they live here rather than in
``indicators.py``; parameters still resolve from ``config/defaults.yaml``
under a ``features`` section (PD-11) - no Python-side defaults anywhere.

These labels are documented, engine-defined metadata for the event payload
(section 6.2). They are never a threshold, filter or trading rule (D-017).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import polars as pl

from engine.config.defaults import load_defaults, resolve_params
from engine.errors import ValidationError
from engine.features.indicators import compute_indicator
from engine.features.primitives import atr_percentile, column, ema, rma, true_range

Tags = list[str | None]


def _resolve(
    section: str,
    params: Mapping[str, Any] | None,
    defaults: Mapping[str, Any] | None,
    positive: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve one ``features.<section>`` parameter block (PD-11)."""
    document = dict(defaults) if defaults is not None else load_defaults()
    resolved = resolve_params(document, f"features.{section}", params)
    for key in positive:
        value = resolved[key]
        if type(value) is not int or value < 1:
            raise ValidationError(
                f"parameter '{key}' of '{section}' must be an integer >= 1",
                detail={"param": key, "value": value},
            )
    return document, resolved


def trend_state(
    frame: pl.DataFrame,
    params: Mapping[str, Any] | None = None,
    *,
    defaults: Mapping[str, Any] | None = None,
) -> Tags:
    """``"up"`` / ``"down"`` / ``"neutral"`` from close against the regime EMA.

    The reference study's own rule family (``rvol_climax_pro_v5.pine`` line
    123, ``inDown = close < emaR and emaR < emaR[5]``): ``"down"`` when close
    is below the EMA *and* the EMA is below itself ``slope_lag`` bars ago,
    ``"up"`` the mirror, otherwise ``"neutral"``. ``null`` before index
    ``slope_lag`` - the slope cannot be measured yet - and wherever the EMA is
    ``null``.
    """
    _, resolved = _resolve("trend_state", params, defaults, ("length", "slope_lag"))
    length = int(resolved["length"])
    lag = int(resolved["slope_lag"])
    close = column(frame, "close")
    averages = ema(close, length)
    out: Tags = [None] * len(close)
    for index in range(lag, len(close)):
        current = averages[index]
        previous = averages[index - lag]
        if current is None or previous is None:
            continue
        price = close[index]
        if price < current and current < previous:
            out[index] = "down"
        elif price > current and current > previous:
            out[index] = "up"
        else:
            out[index] = "neutral"
    return out


def vol_state(
    frame: pl.DataFrame,
    params: Mapping[str, Any] | None = None,
    *,
    defaults: Mapping[str, Any] | None = None,
) -> Tags:
    """``"low"`` / ``"mid"`` / ``"high"`` from the ATR percentile (design s2).

    Percentile rank of ``atr(atr_length)[t]`` inside the trailing ``window``
    bars: ``"low"`` below 33, ``"mid"`` below 67, ``"high"`` otherwise. The
    33/67 edges are descriptive, engine-defined (labelled so in
    config/defaults.yaml); ``null`` until a full window of ATR values exists.
    """
    _, resolved = _resolve("vol_state", params, defaults, ("atr_length", "window"))
    ranges = true_range(
        column(frame, "high"), column(frame, "low"), column(frame, "close")
    )
    percentiles = atr_percentile(
        rma(ranges, int(resolved["atr_length"])), int(resolved["window"])
    )
    out: Tags = [None] * len(percentiles)
    for index, percentile in enumerate(percentiles):
        if percentile is None:
            continue
        out[index] = "low" if percentile < 33 else "mid" if percentile < 67 else "high"
    return out


def volume_state(
    frame: pl.DataFrame,
    params: Mapping[str, Any] | None = None,
    *,
    defaults: Mapping[str, Any] | None = None,
) -> Tags:
    """Volume buckets from the registry's own ``rvol`` (single definition).

    ``"low"`` < 0.5, ``"normal"`` < 1.0, ``"elevated"`` < 2.0, ``"high"`` <
    3.0, ``"climax"`` >= 3.0: the top two edges are the section 6.3
    thresholds (``top_mult`` 2.0, ``climax_mult`` 3.0), the lower two are
    descriptive.     ``null`` wherever ``rvol`` is ``null`` (warm-up).
    """
    document, resolved = _resolve(
        "volume_state", params, defaults, ("length",)
    )
    values = compute_indicator(
        frame,
        name="rvol",
        params={"length": int(resolved["length"])},
        defaults=document,
    )["series"][0]["values"]
    out: Tags = [None] * len(values)
    for index, value in enumerate(values):
        if value is None:
            continue
        out[index] = (
            "low"
            if value < 0.5
            else "normal"
            if value < 1.0
            else "elevated"
            if value < 2.0
            else "high"
            if value < 3.0
            else "climax"
        )
    return out
