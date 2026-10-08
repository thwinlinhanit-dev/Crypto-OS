"""Section 14.2 layer-1 CSV import: TradingView chart data -> ReferenceExport.

TradingView's chart-data export header spellings vary by locale and version,
so columns map through an alias table (the Pine plot titles of
``specs/reference/rvol_climax_pro_v5.pine`` included) and anything unrecognised
is refused - no silent coercion, PD-10 for data. ``#``, an empty cell, ``NaN``
and ``n/a`` in a *plot* column import as ``null`` (Pine ``na``); the same token
in OHLCV, an unparsable time, a missing required column, a duplicate or a
non-increasing timestamp reject the export with a precise error. A rejected
import classifies the run ``UNKNOWN`` (design note 4.2): "cannot be compared"
must never read as "passed".
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from engine.errors import ValidationError

#: The layer-1 plots spec line 590 names, keyed by their canonical name.
REQUIRED_PLOTS: tuple[str, ...] = ("rvol", "cpos", "upper_wick", "ema_regime")

#: Structural columns every export must carry (spec line 590 "OHLCV + ...").
REQUIRED_BARS: tuple[str, ...] = ("time", "open", "high", "low", "close", "volume")

#: Tokens TradingView writes for Pine ``na`` in a plot column (design note 4.2).
NA_TOKENS: frozenset[str] = frozenset({"#", "", "nan", "n/a"})

#: Accepted timestamp spellings; all are read as UTC (naive values are UTC).
_TIME_FORMATS: tuple[str, ...] = (
    "%Y.%m.%d %H:%M",
    "%Y.%m.%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y.%m.%d",
    "%Y-%m-%d",
)


def _normalise(header: str) -> str:
    return re.sub(r"\s+", " ", header.strip().lower().replace("_", " "))


#: Canonical column -> accepted header spellings (normalised). The Pine plot
#: titles are the authority for the four plots (lines 396, 400-402).
COLUMN_ALIASES: dict[str, frozenset[str]] = {
    "time": frozenset({"time", "date", "datetime", "timestamp", "bar time"}),
    "open": frozenset({"open"}),
    "high": frozenset({"high"}),
    "low": frozenset({"low"}),
    "close": frozenset({"close"}),
    "volume": frozenset({"volume"}),
    "rvol": frozenset({"rvol"}),
    "cpos": frozenset({"cpos", "close position"}),
    "upper_wick": frozenset({"upper wick share", "upper wick", "upw"}),
    "ema_regime": frozenset({"regime ema", "ema regime"}),
}

_ALIAS_TO_COLUMN: dict[str, str] = {
    alias: column for column, aliases in COLUMN_ALIASES.items() for alias in aliases
}


@dataclass(frozen=True, slots=True)
class ReferenceBar:
    """One imported export row: OHLCV plus the four layer-1 plot values."""

    time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    plots: dict[str, float | None]
    #: The raw text each plot value was written as, so the rounding
    #: platform difference can measure the export's precision (design note 4.3).
    plot_raw: dict[str, str]


@dataclass(frozen=True, slots=True)
class ReferenceExport:
    """A parsed chart-data export, ordered exactly as the file is."""

    source: str
    bars: tuple[ReferenceBar, ...]


def parse_time_ms(raw: str) -> int:
    """Epoch seconds / epoch milliseconds / ISO-8601 / TradingView date text."""
    text = raw.strip()
    if not text:
        raise ValidationError("unparsable time in reference export", {"value": raw})
    if re.fullmatch(r"\d{10}", text):
        return int(text) * 1000
    if re.fullmatch(r"\d{13}", text):
        return int(text)
    iso = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        for fmt in _TIME_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValidationError("unparsable time in reference export", {"value": raw})
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return round(parsed.timestamp() * 1000)


def parse_bar_float(raw: str, *, column: str, line: int) -> float:
    """OHLCV must be a finite number; ``na`` in the bar itself rejects (4.2)."""
    text = raw.strip()
    if text.lower() in NA_TOKENS:
        raise ValidationError(
            "null value in an OHLCV column of the reference export",
            {"column": column, "line": line, "value": raw},
        )
    try:
        value = float(text)
    except ValueError as exc:
        raise ValidationError(
            "unparsable number in the reference export",
            {"column": column, "line": line, "value": raw},
        ) from exc
    if not math.isfinite(value):
        raise ValidationError(
            "non-finite number in the reference export",
            {"column": column, "line": line, "value": raw},
        )
    return value


def significant_digits(raw: str) -> int | None:
    """Decimal digits TradingView wrote (``1.234`` -> 4, ``0.00120`` -> 3).

    Used by the ``tv_export_rounding`` platform difference: a value that differs
    only beyond this precision was rounded on the way out, not computed apart.
    """
    text = raw.strip()
    if not text or text.lower() in NA_TOKENS:
        return None
    mantissa = re.split(r"[eE]", text)[0].lstrip("+-")
    digits = mantissa.replace(".", "").lstrip("0")
    if not digits:
        return None
    return len(digits)


def _import_error(message: str, detail: dict[str, object]) -> ValidationError:
    return ValidationError(message, detail)


def import_chart_csv(path: Path) -> ReferenceExport:
    """Parse a TradingView chart-data export; every problem rejects it (4.2)."""
    if not path.is_file():
        raise _import_error("reference export not found", {"path": path.as_posix()})
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise _import_error("reference export is unreadable", {"path": path.as_posix()}) from exc

    rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if not rows:
        raise _import_error("reference export is empty", {"path": path.as_posix()})

    header = rows[0]
    index_of: dict[str, int] = {}
    for position, raw_name in enumerate(header):
        column = _ALIAS_TO_COLUMN.get(_normalise(raw_name))
        if column is None:
            raise _import_error(
                "unknown column in the reference export",
                {"column": raw_name, "known": sorted(set(_ALIAS_TO_COLUMN))},
            )
        if column in index_of:
            raise _import_error(
                "duplicate column in the reference export",
                {"column": raw_name, "canonical": column},
            )
        index_of[column] = position
    missing = [name for name in (*REQUIRED_BARS, *REQUIRED_PLOTS) if name not in index_of]
    if missing:
        raise _import_error(
            "missing required column in the reference export",
            {"missing": missing, "path": path.as_posix()},
        )

    expected_width = len(header)
    bars: list[ReferenceBar] = []
    for line, row in enumerate(rows[1:], start=2):
        if len(row) != expected_width:
            raise _import_error(
                "row width does not match the header in the reference export",
                {"line": line, "cells": len(row), "header_cells": expected_width},
            )

        def cell(column: str, cells: list[str] = row) -> str:
            return cells[index_of[column]]

        plots: dict[str, float | None] = {}
        plot_raw: dict[str, str] = {}
        for name in REQUIRED_PLOTS:
            raw_value = cell(name).strip()
            plot_raw[name] = raw_value
            if raw_value.lower() in NA_TOKENS:
                plots[name] = None
            else:
                plots[name] = parse_bar_float(raw_value, column=name, line=line)
        bars.append(
            ReferenceBar(
                time_ms=parse_time_ms(cell("time")),
                open=parse_bar_float(cell("open"), column="open", line=line),
                high=parse_bar_float(cell("high"), column="high", line=line),
                low=parse_bar_float(cell("low"), column="low", line=line),
                close=parse_bar_float(cell("close"), column="close", line=line),
                volume=parse_bar_float(cell("volume"), column="volume", line=line),
                plots=plots,
                plot_raw=plot_raw,
            )
        )

    if not bars:
        raise _import_error("reference export has no bars", {"path": path.as_posix()})
    for earlier, later in pairwise(bars):
        if later.time_ms <= earlier.time_ms:
            raise _import_error(
                "reference export time is not strictly increasing",
                {"earlier": earlier.time_ms, "later": later.time_ms},
            )
    return ReferenceExport(source=path.as_posix(), bars=tuple(bars))
