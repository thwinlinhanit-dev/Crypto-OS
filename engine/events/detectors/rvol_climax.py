"""RVOL_CLIMAX v5.0 (spec section 6.3, design note section 4).

Port of the Pine detection block (specs/reference/rvol_climax_pro_v5.pine
lines 75-146) onto the M2/M3 primitives - one implementation, no
re-derivation. Every derived quantity is pinned to the Pine line that
defines it; comparisons are exact float ``>=``/``>`` (Pine semantics, no
epsilons). The four raw streams pass through the shared Section 6.7 cooldown
gate independently (D-022); emitted signals become Events.

Naming note (spec line 269): event type ``TOP`` is NOT ``TOP_CAPIT``,
``TOP_CLIMAX``, ``TOPFAIL`` or any distribution/exhaustion concept. Strategy
flags (``use_long_cont`` etc.) and side mode belong to the strategy
definition (Section 7), never to the detector: this detector emits all four
types independently of strategy flags (spec line 252).

Default ``cluster_model`` is a documented ASSUMPTION for M5 stats (the
detector does not apply clustering).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, ClassVar

import polars as pl

from engine.config.defaults import load_defaults, resolve_params
from engine.data.bars import parse_timeframe
from engine.errors import ValidationError
from engine.events.base import Event, event_time_from_open, params_hash
from engine.events.cooldown import apply_cooldown
from engine.features.primitives import (
    column,
    ema,
    highest,
    lowest,
    rma,
    rolling_mean,
    true_range,
)
from engine.versions import ENGINE_VERSION

#: Event types this detector emits (spec R7: CONT/CAPIT/FAIL/TOP).
EVENT_TYPES = ("CONT", "CAPIT", "FAIL", "TOP")

#: Direction per type (R11: FAIL/TOP are research events, not strategy shorts).
DIRECTION_BY_TYPE: dict[str, str] = {
    "CONT": "LONG",
    "CAPIT": "LONG",
    "FAIL": "SHORT",
    "TOP": "SHORT",
}

#: fail_mode vocabulary (defaults.yaml + OQ-2).
FAIL_MODES = ("failed_bounce", "legacy_v1")

#: Invalidation ATR multiple (spec line 268; Pine atrStopMult default).
INVALIDATION_ATR_MULT = 3.0

_ARCHETYPE_NAMES: dict[int, str] = {
    1: "top·drive high",
    2: "top·sell pressure",
    3: "top·mid",
    4: "bot·drive low",
    5: "bot·cover press",
    6: "bot·mid",
}


def _lag_one(values: list[float | None]) -> list[float | None]:
    """``values[t-1]``; None at t=0 (Pine ``[1]``)."""
    return [None, *values[:-1]]


class RvolClimaxDetector:
    """Section 6.3 detector; registry entry is the module-level ``DETECTOR``."""

    detector_id = "RVOL_CLIMAX"
    version = "5.0"
    required_inputs = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_time",
        "timeframe",
    )
    warmup_bars = 100
    lookback_bars = 100
    default_cluster_model = "SYMBOL_GAP + CALENDAR_WEEK"
    defaults_key = "detectors.rvol_climax_v5"
    categorical: ClassVar[dict[str, tuple[str, ...]]] = {"fail_mode": FAIL_MODES}
    positive_int_params = (
        "vol_len",
        "range_len",
        "trend_len",
        "regime_len",
        "bounce_len",
        "cooldown",
        "atr_len",
        "lookback_bars",
    )

    def resolve(
        self,
        overrides: Mapping[str, Any] | None = None,
        defaults: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PD-11: parameters from request or config/defaults.yaml only."""
        document = dict(defaults) if defaults is not None else load_defaults()
        resolved = resolve_params(document, self.defaults_key, overrides)
        for key in self.positive_int_params:
            value = resolved[key]
            if type(value) is not int or value < 0 or (key != "cooldown" and value < 1):
                raise ValidationError(
                    f"parameter '{key}' of '{self.detector_id}' must be an "
                    f"integer >= {'0' if key == 'cooldown' else '1'}",
                    detail={"param": key, "value": value},
                )
        if resolved["fail_mode"] not in FAIL_MODES:
            raise ValidationError(
                f"parameter 'fail_mode' must be one of {', '.join(FAIL_MODES)}",
                detail={"param": "fail_mode", "value": resolved["fail_mode"]},
            )
        for key in ("climax_mult", "top_mult", "bounce_atr", "wick_min"):
            value = resolved[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValidationError(
                    f"parameter '{key}' must be a non-negative number",
                    detail={"param": key, "value": value},
                )
        for key in ("close_pos_lo", "close_pos_hi"):
            value = resolved[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not (
                0.0 <= value <= 1.0
            ):
                raise ValidationError(
                    f"parameter '{key}' must be in [0, 1]",
                    detail={"param": key, "value": value},
                )
        if float(resolved["close_pos_lo"]) > float(resolved["close_pos_hi"]):
            raise ValidationError(
                "close_pos_lo must be <= close_pos_hi",
                detail={
                    "close_pos_lo": resolved["close_pos_lo"],
                    "close_pos_hi": resolved["close_pos_hi"],
                },
            )
        return resolved

    def derived(
        self, frame: pl.DataFrame, resolved: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Every derived quantity, for detection and for the hover card.

        Lag semantics pinned to Pine: ``volMA``/``hiN``/``loN``/``swingLo``
        are ``[1]``-lagged; ``emaR`` is not. ``inDown`` compares ``emaR``
        against ``emaR[trend_len]`` (Pine hard-codes ``[5]`` = default
        ``trend_len``; using the parameter keeps one source of truth).
        """
        vol_len = int(resolved["vol_len"])
        range_len = int(resolved["range_len"])
        trend_len = int(resolved["trend_len"])
        regime_len = int(resolved["regime_len"])
        bounce_len = int(resolved["bounce_len"])
        atr_len = int(resolved["atr_len"])
        climax_mult = float(resolved["climax_mult"])
        top_mult = float(resolved["top_mult"])
        close_pos_lo = float(resolved["close_pos_lo"])
        close_pos_hi = float(resolved["close_pos_hi"])
        bounce_atr = float(resolved["bounce_atr"])
        wick_min = float(resolved["wick_min"])

        open_ = column(frame, "open")
        high = column(frame, "high")
        low = column(frame, "low")
        close = column(frame, "close")
        volume = column(frame, "volume")

        atr_series = rma(true_range(high, low, close), atr_len)
        vol_ma = _lag_one(rolling_mean(volume, vol_len))
        rvol: list[float | None] = [
            v / ma if ma is not None and ma > 0.0 else None
            for v, ma in zip(volume, vol_ma, strict=True)
        ]
        hi_n = _lag_one(highest(high, range_len))
        lo_n = _lag_one(lowest(low, range_len))
        ema_r = ema(close, regime_len)
        swing_lo = _lag_one(lowest(low, bounce_len))

        size = len(close)
        cpos: list[float | None] = [None] * size
        up_w: list[float | None] = [None] * size
        trend_u: list[bool | None] = [None] * size
        at_hi: list[bool | None] = [None] * size
        at_lo: list[bool | None] = [None] * size
        near_hi: list[bool | None] = [None] * size
        is_climax: list[bool | None] = [None] * size
        archetype: list[str | None] = [None] * size
        in_down: list[bool | None] = [None] * size
        bounced: list[bool | None] = [None] * size
        rejected: list[bool | None] = [None] * size
        top_reject: list[bool | None] = [None] * size
        raw_top: list[bool] = [False] * size
        raw_cont: list[bool] = [False] * size
        raw_cap: list[bool] = [False] * size
        raw_fail_new: list[bool] = [False] * size
        raw_fail_legacy: list[bool] = [False] * size

        for index in range(size):
            rng = high[index] - low[index]
            cpos_val = (close[index] - low[index]) / rng if rng > 0.0 else 0.5
            cpos[index] = cpos_val
            up_w_val = (
                (high[index] - max(open_[index], close[index])) / rng
                if rng > 0.0
                else 0.0
            )
            up_w[index] = up_w_val
            if index >= trend_len:
                trend_u[index] = close[index] > close[index - trend_len]
            hn = hi_n[index]
            lo = lo_n[index]
            if hn is not None:
                at_hi[index] = close[index] >= hn
                near_hi[index] = high[index] >= hn
            if lo is not None:
                at_lo[index] = close[index] <= lo
            rv = rvol[index]
            if rv is not None:
                is_climax[index] = rv >= climax_mult
                if is_climax[index]:
                    is_up = trend_u[index]
                    if is_up is None:
                        continue
                    if is_up:
                        if cpos_val >= close_pos_hi:
                            archetype[index] = _ARCHETYPE_NAMES[1]
                        elif cpos_val <= close_pos_lo:
                            archetype[index] = _ARCHETYPE_NAMES[2]
                        else:
                            archetype[index] = _ARCHETYPE_NAMES[3]
                    else:
                        if cpos_val <= close_pos_lo:
                            archetype[index] = _ARCHETYPE_NAMES[4]
                        elif cpos_val >= close_pos_hi:
                            archetype[index] = _ARCHETYPE_NAMES[5]
                        else:
                            archetype[index] = _ARCHETYPE_NAMES[6]
            # inDown / bounced / rejected (Pine 123-125)
            er = ema_r[index]
            if er is not None and index >= trend_len:
                er_lag = ema_r[index - trend_len]
                if er_lag is not None:
                    in_down[index] = close[index] < er and er < er_lag
            sl = swing_lo[index]
            at = atr_series[index]
            if sl is not None and at is not None:
                bounced[index] = high[index] - sl >= bounce_atr * at
            rejected_val = close[index] < open_[index] and up_w_val >= wick_min
            rejected[index] = rejected_val
            top_reject_val = close[index] < open_[index] and (
                up_w_val >= wick_min or cpos_val <= close_pos_lo
            )
            top_reject[index] = top_reject_val
            # Raw rules (Pine 127-135); every input must be known.
            climax = is_climax[index]
            up = trend_u[index]
            hi_flag = at_hi[index]
            lo_flag = at_lo[index]
            near = near_hi[index]
            c_above = er is not None and close[index] > er
            if (
                climax
                and near is not None
                and c_above
                and top_reject_val
                and rv is not None
            ):
                raw_top[index] = bool(rv >= top_mult and near and top_reject_val)
            if (
                climax
                and up is True
                and hi_flag is True
                and not raw_top[index]
            ):
                raw_cont[index] = True
            if (
                climax
                and up is False
                and cpos_val <= close_pos_lo
                and lo_flag is True
            ):
                raw_cap[index] = True
            # newFail / legacyFail (Pine 133-135)
            if (
                climax
                and in_down[index] is True
                and bounced[index] is True
                and rejected_val
                and lo_flag is not True
            ):
                raw_fail_new[index] = True
            if climax and up is False and cpos_val > close_pos_lo:
                raw_fail_legacy[index] = True

        fail_mode = str(resolved["fail_mode"])
        raw_fail = raw_fail_new if fail_mode == "failed_bounce" else raw_fail_legacy

        return {
            "atr": atr_series,
            "volMA": vol_ma,
            "rvol": rvol,
            "hiN": hi_n,
            "loN": lo_n,
            "emaR": ema_r,
            "swingLo": swing_lo,
            "cpos": cpos,
            "upW": up_w,
            "trendU": trend_u,
            "atHi": at_hi,
            "atLo": at_lo,
            "nearHi": near_hi,
            "isClimax": is_climax,
            "archetype": archetype,
            "inDown": in_down,
            "bounced": bounced,
            "rejected": rejected,
            "topReject": top_reject,
            "closeAboveEmaR": [
                er is not None and close[i] > er for i, er in enumerate(ema_r)
            ],
            "rawCONT": raw_cont,
            "rawCAPIT": raw_cap,
            "rawFAIL": raw_fail,
            "rawTOP": raw_top,
        }

    def detect(
        self,
        frame: pl.DataFrame,
        resolved_params: Mapping[str, Any],
        exclusions: Sequence[Any] = (),
        *,
        instrument_id: str = "",
        dataset_version: str = "",
        data_quality: Mapping[str, Any] | None = None,
        timeframe: str = "1D",
    ) -> list[Event]:
        """Scan closed bars; return emitted events in bar order (PD-2)."""
        size = frame.height
        if size == 0:
            return []
        open_times = [int(value) for value in frame["open_time"].to_list()]
        close = column(frame, "close")
        series = self.derived(frame, resolved_params)
        cooldown = int(resolved_params["cooldown"])
        lookback = int(resolved_params["lookback_bars"])
        phash = params_hash(resolved_params)
        quality = dict(data_quality or {})

        exclusion_ms = [
            (_to_ms(start), _to_ms(end)) for start, end in exclusions
        ]

        emitted: dict[str, list[bool]] = {}
        for event_type, raw_key in (
            ("CONT", "rawCONT"),
            ("CAPIT", "rawCAPIT"),
            ("FAIL", "rawFAIL"),
            ("TOP", "rawTOP"),
        ):
            emitted[event_type] = apply_cooldown(series[raw_key], cooldown)

        events: list[Event] = []
        for index in range(size):
            if index < lookback:
                continue
            for event_type in EVENT_TYPES:
                if not emitted[event_type][index]:
                    continue
                rv = series["rvol"][index]
                atr_value = series["atr"][index]
                if rv is None or atr_value is None:
                    continue
                direction = DIRECTION_BY_TYPE[event_type]
                ref = close[index]
                invalidation = (
                    ref - INVALIDATION_ATR_MULT * atr_value
                    if direction == "LONG"
                    else ref + INVALIDATION_ATR_MULT * atr_value
                )
                event_time = event_time_from_open(open_times[index], timeframe)
                rules = _trigger_rules(event_type, series, index, resolved_params)
                events.append(
                    Event(
                        detector_id=self.detector_id,
                        event_type=event_type,
                        version=self.version,
                        params_hash=phash,
                        instrument_id=instrument_id,
                        timeframe=timeframe,
                        event_time=event_time,
                        direction=direction,
                        ref_price=ref,
                        invalidation_price=invalidation,
                        regime=_regime_tags(frame, index),
                        features={
                            "rvol": rv,
                            "cpos": series["cpos"][index],
                            "upper_wick": series["upW"][index],
                            "atr": atr_value,
                            "ema_regime": series["emaR"][index],
                            "archetype": series["archetype"][index],
                        },
                        trigger={"rules": rules},
                        lookback_gap=_lookback_gap(
                            open_times[index], lookback, timeframe, exclusion_ms
                        ),
                        dataset_version=dataset_version,
                        engine_version=ENGINE_VERSION,
                        data_quality=quality,
                        resolved_params=dict(resolved_params),
                        event_index=index,
                    )
                )
        events.sort(key=lambda e: (e.event_index, EVENT_TYPES.index(e.event_type)))
        return events


def _to_ms(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    return int(value)


def _lookback_gap(
    open_time_ms: int,
    lookback: int,
    timeframe: str,
    exclusion_ms: Sequence[tuple[int, int]],
) -> bool:
    """True when [event_index - lookback, event_index] overlaps an exclusion."""
    if not exclusion_ms:
        return False
    from engine.data.bars import BAR_MS

    duration = BAR_MS[parse_timeframe(timeframe)]
    start = open_time_ms - lookback * duration
    end = open_time_ms + duration  # inclusive of the event bar's close
    return any(start < ex_end and ex_start < end for ex_start, ex_end in exclusion_ms)


def _regime_tags(frame: pl.DataFrame, index: int) -> dict[str, Any]:
    """Descriptive regime tags at the event bar (D-017: never a filter)."""
    from engine.features.regime import trend_state, vol_state, volume_state

    return {
        "trend_state": trend_state(frame)[index],
        "vol_state": vol_state(frame)[index],
        "volume_state": volume_state(frame)[index],
    }


def _rule(name: str, passed: bool | None, value: Any, threshold: Any) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed) if passed is not None else False,
        "value": value,
        "threshold": threshold,
    }


def _trigger_rules(
    event_type: str,
    series: Mapping[str, Any],
    index: int,
    resolved: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """The checklist shown on the hover card (PD-5: numbers from the engine)."""
    rv = series["rvol"][index]
    climax_mult = float(resolved["climax_mult"])
    top_mult = float(resolved["top_mult"])
    rules = [_rule("isClimax", series["isClimax"][index], rv, climax_mult)]
    if event_type == "CONT":
        rules.append(_rule("trendU", series["trendU"][index], series["trendU"][index], True))
        rules.append(_rule("atHi", series["atHi"][index], series["atHi"][index], True))
        rules.append(_rule("not rawTOP", True, True, True))
    elif event_type == "CAPIT":
        rules.append(_rule("not trendU", series["trendU"][index] is False, series["trendU"][index], False))
        rules.append(
            _rule(
                "cpos <= close_pos_lo",
                series["cpos"][index] <= float(resolved["close_pos_lo"]),
                series["cpos"][index],
                float(resolved["close_pos_lo"]),
            )
        )
        rules.append(_rule("atLo", series["atLo"][index], series["atLo"][index], True))
    elif event_type == "TOP":
        rules.append(
            _rule("rvol >= top_mult", rv is not None and rv >= top_mult, rv, top_mult)
        )
        rules.append(_rule("close > emaR", series["closeAboveEmaR"][index], series["closeAboveEmaR"][index], True))
        rules.append(_rule("nearHi", series["nearHi"][index], series["nearHi"][index], True))
        rules.append(_rule("topReject", series["topReject"][index], series["topReject"][index], True))
    elif event_type == "FAIL":
        if resolved["fail_mode"] == "failed_bounce":
            rules.append(_rule("inDown", series["inDown"][index], series["inDown"][index], True))
            rules.append(_rule("bounced", series["bounced"][index], series["bounced"][index], True))
            rules.append(_rule("rejected", series["rejected"][index], series["upW"][index], float(resolved["wick_min"])))
            rules.append(_rule("not atLo", series["atLo"][index] is not True, series["atLo"][index], False))
        else:
            rules.append(_rule("not trendU", series["trendU"][index] is False, series["trendU"][index], False))
            rules.append(
                _rule(
                    "cpos > close_pos_lo",
                    series["cpos"][index] > float(resolved["close_pos_lo"]),
                    series["cpos"][index],
                    float(resolved["close_pos_lo"]),
                )
            )
    return rules


DETECTOR = RvolClimaxDetector()
