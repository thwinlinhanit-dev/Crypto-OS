"""Data scopes (spec section 8.1): frozen DEVELOPMENT/HOLDOUT boundaries and
the clip / embargo / purge enforcement every stats, event-study, backtest and
validation request runs through.

Boundary provenance (OD-8, section 8.1):

* ``S = [earliest first-bar open_time, latest closed bar at freeze time]`` for
  the dataset family defined in ``config/dataset_scopes.yaml``;
* ``holdout_start = S_start + 0.80 x span`` and ``oos_start = S_start +
  0.56 x span``, each floored to the UTC calendar day containing the instant;
* computed ONCE at M5 and frozen: this module reads them, applies them, and -
  through :func:`freeze_scope` - refuses to write a family that already exists.
  A new universe is a NEW entry (and a DECISIONS record), never an edit.

Everything fails closed: an unknown scope family, a missing scope file, a
malformed entry or a window that does not intersect the requested scope raises
``ValidationError`` (PD-10). No segment is ever guessed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from engine.errors import ImmutableResultError, ValidationError

DAY_MS = 86_400_000

#: The only source of scope definitions (PD-11): request or this config file.
SCOPE_FILE = Path("config") / "dataset_scopes.yaml"

#: Section 8.1: DEVELOPMENT = first 80% of the calendar span (IS = first 70%
#: of DEVELOPMENT, hence OOS starts at 0.80 x 0.70 = 0.56 of the full span).
#: Percentages are exact integers so the frozen dates carry no float drift.
HOLDOUT_PERCENT = 80
OOS_PERCENT = 56

DEVELOPMENT = "DEVELOPMENT"
HOLDOUT = "HOLDOUT"
SCOPES: tuple[str, ...] = (DEVELOPMENT, HOLDOUT)

#: Section 8.1 warning code: a request window was clipped to its scope.
SCOPE_CLIPPED = "SCOPE_CLIPPED"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class ScopeBoundaries:
    """One frozen entry of ``config/dataset_scopes.yaml`` (all times in ms)."""

    family: str
    universe: tuple[str, ...]
    timeframes: tuple[str, ...]
    s_start: int
    s_end: int
    oos_start: int
    holdout_start: int
    span_days: int
    frozen_at: str
    frozen_by: str


@dataclass(frozen=True)
class ClippedWindow:
    """Result of scoping a requested window (spec 8.1 "clipped and flagged")."""

    applied_start: int
    applied_end: int
    scope: str
    clipped: bool
    flag: str | None  # SCOPE_CLIPPED when anything was trimmed, else None


@dataclass(frozen=True)
class OriginAssignment:
    """One observation that survived embargo/purge, with its single segment."""

    origin_ms: int
    segment: str


@dataclass(frozen=True)
class PurgeReport:
    """Partition of the input origins: kept + embargoed + purged == input."""

    kept: tuple[OriginAssignment, ...]
    embargoed: tuple[int, ...]
    purged: tuple[int, ...]


def floor_utc_day(ms: int) -> int:
    """Start of the UTC calendar day containing ``ms`` (spec 8.1 rounding)."""
    return (ms // DAY_MS) * DAY_MS


def compute_boundaries(s_start_ms: int, s_end_ms: int) -> tuple[int, int]:
    """Raw ``(oos_start, holdout_start)`` instants for an S range, before flooring.

    ``holdout_start = S_start + 0.80 x span`` and ``oos_start = S_start +
    0.56 x span`` (section 8.1), computed in exact integer arithmetic:
    ``span * 80 // 100`` and ``span * 56 // 100`` are the floor of the decimal
    products, so a float literal can never shift a frozen boundary by a
    truncated millisecond.
    """
    span = s_end_ms - s_start_ms
    if s_start_ms < 0 or span <= 0:
        raise ValidationError(
            "dataset span must be positive",
            {"s_start": s_start_ms, "s_end": s_end_ms},
        )
    return (
        s_start_ms + (span * OOS_PERCENT) // 100,
        s_start_ms + (span * HOLDOUT_PERCENT) // 100,
    )


def segment_of(origin_ms: int, boundaries: ScopeBoundaries) -> str:
    """Segment of an origin time: IS / OOS / HOLDOUT (spec 8.1 assignment)."""
    if origin_ms < boundaries.oos_start:
        return "IS"
    if origin_ms < boundaries.holdout_start:
        return "OOS"
    return "HOLDOUT"


def clip_window(
    start_ms: int,
    end_ms: int,
    *,
    boundaries: ScopeBoundaries,
    scope: str,
) -> ClippedWindow:
    """Clip a requested half-open ``[start, end)`` window to its data scope.

    ``DEVELOPMENT`` is ``[s_start, holdout_start)``; ``HOLDOUT`` is
    ``[holdout_start, s_end]``. Anything trimmed is flagged ``SCOPE_CLIPPED``
    (the caller surfaces it as a warning - the clip is never silent). A window
    that does not intersect the scope at all is a validation error, not an
    empty result (PD-10).
    """
    if scope not in SCOPES:
        raise ValidationError(
            f"data_scope must be one of {list(SCOPES)}",
            {"data_scope": scope, "allowed": list(SCOPES)},
        )
    if start_ms >= end_ms:
        raise ValidationError(
            "window start must precede window end",
            {"start_ms": start_ms, "end_ms": end_ms},
        )
    if scope == DEVELOPMENT:
        low, high = boundaries.s_start, boundaries.holdout_start
    else:
        low, high = boundaries.holdout_start, boundaries.s_end
    applied_start = max(start_ms, low)
    applied_end = min(end_ms, high)
    if applied_start >= applied_end:
        raise ValidationError(
            f"window does not intersect data_scope {scope}",
            {
                "data_scope": scope,
                "requested": [start_ms, end_ms],
                "scope_window": [low, high],
            },
        )
    clipped = (applied_start, applied_end) != (start_ms, end_ms)
    return ClippedWindow(
        applied_start=applied_start,
        applied_end=applied_end,
        scope=scope,
        clipped=clipped,
        flag=SCOPE_CLIPPED if clipped else None,
    )


def embargo_purge(
    origins: Sequence[int],
    *,
    boundaries: ScopeBoundaries,
    horizon_h: int,
    embargo_e: int,
    bar_ms: int,
) -> PurgeReport:
    """Apply the exact embargo/purge order of spec 8.1 to observation origins.

    1. EMBARGO - origins in ``[b, b + E bars)`` of the LATER segment (both
       boundaries) are unused as observation origins.
    2. PURGE - origins with ``t < b`` whose ``H``-bar outcome window would
       cross ``b`` (``t + H > b``) are removed from the EARLIER segment.
    3. SURVIVORS - assigned to exactly one segment by origin time.

    Removing observations never moves a boundary or re-labels a segment.
    """
    if horizon_h < 0 or embargo_e < 0:
        raise ValidationError(
            "horizon_h and embargo_e must be non-negative",
            {"horizon_h": horizon_h, "embargo_e": embargo_e},
        )
    if bar_ms <= 0:
        raise ValidationError("bar_ms must be positive", {"bar_ms": bar_ms})
    embargo_ms = embargo_e * bar_ms
    horizon_ms = horizon_h * bar_ms
    bounds = (boundaries.oos_start, boundaries.holdout_start)
    kept: list[OriginAssignment] = []
    embargoed: list[int] = []
    purged: list[int] = []
    for origin in sorted(origins):
        if any(b <= origin < b + embargo_ms for b in bounds):
            embargoed.append(origin)
        elif any(origin < b < origin + horizon_ms for b in bounds):
            purged.append(origin)
        else:
            kept.append(OriginAssignment(origin_ms=origin, segment=segment_of(origin, boundaries)))
    return PurgeReport(kept=tuple(kept), embargoed=tuple(embargoed), purged=tuple(purged))


def boundaries_payload(boundaries: ScopeBoundaries, scope: str) -> dict[str, str]:
    """Section 8.1: "every result shows data_scope and boundaries used"."""
    return {
        "data_scope": scope,
        "oos_start": _to_iso(boundaries.oos_start),
        "holdout_start": _to_iso(boundaries.holdout_start),
    }


# --- config file access -------------------------------------------------------------


def default_family(path: Path = SCOPE_FILE) -> str:
    """The configured default dataset family (fail-closed when absent)."""
    raw = _read_yaml(path)
    family = raw.get("default_family")
    if not isinstance(family, str) or not family:
        raise ValidationError(
            f"{path} defines no default_family",
            {"path": str(path)},
        )
    return family


def load_boundaries(family: str | None = None, path: Path = SCOPE_FILE) -> ScopeBoundaries:
    """Read one frozen scope entry; ``None`` selects ``default_family``.

    Every field is required and type-checked: there are no defaults to invent
    (PD-11) and no partial scope that could half-apply (PD-10).
    """
    raw = _read_yaml(path)
    name = family if family is not None else raw.get("default_family")
    if not isinstance(name, str) or not name:
        raise ValidationError(
            f"scope family not specified and {path} defines no default_family",
            {"path": str(path)},
        )
    scopes = raw.get("scopes")
    entry = scopes.get(name) if isinstance(scopes, dict) else None
    if not isinstance(entry, dict):
        defined = sorted(scopes) if isinstance(scopes, dict) else []
        raise ValidationError(
            f"scope family '{name}' is not defined in {path}",
            {"family": name, "path": str(path), "defined_families": defined},
        )
    return _parse_entry(name, entry, path)


def freeze_scope(boundaries: ScopeBoundaries, path: Path = SCOPE_FILE) -> None:
    """Write one family entry into the scope file.

    Refuses to overwrite an existing family: boundaries never move after
    freeze (spec 8.1); changing the universe/family means a NEW entry plus a
    DECISIONS record.
    """
    raw = _read_yaml(path) if path.is_file() else {}
    scopes = raw.get("scopes")
    if scopes is None:
        scopes = {}
        raw["scopes"] = scopes
    if not isinstance(scopes, dict):
        raise ValidationError(
            f"{path}: 'scopes' must be a mapping of family -> entry",
            {"path": str(path), "type": type(scopes).__name__},
        )
    if boundaries.family in scopes:
        raise ImmutableResultError(
            f"scope family '{boundaries.family}' is already frozen; "
            "boundaries never move after freeze - create a new entry instead",
            {"family": boundaries.family, "path": str(path)},
        )
    scopes[boundaries.family] = _dump_entry(boundaries)
    raw.setdefault("default_family", boundaries.family)
    header = (
        "# config/dataset_scopes.yaml - data-scope definitions (spec section 10,\n"
        "# data_scope values DEVELOPMENT / HOLDOUT; section 16 OD-8 owns the split point).\n"
        "#\n"
        "# Boundaries are frozen once (spec 8.1): computed from S = [earliest first-bar\n"
        "# open_time, latest closed bar], holdout_start = S_start + 0.80 x span and\n"
        "# oos_start = S_start + 0.56 x span, each floored to the containing UTC day.\n"
        "# They never move; new data after the freeze belongs to HOLDOUT automatically.\n"
        "# A new universe/family requires a NEW entry here plus a DECISIONS record.\n\n"
    )
    path.write_text(header + yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


# --- internals ----------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValidationError(f"scope file not found: {path}", {"path": str(path)})
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{path} must hold a mapping",
            {"path": str(path), "type": type(raw).__name__},
        )
    return raw


def _require(entry: dict[str, Any], key: str, path: Path) -> Any:
    if key not in entry:
        raise ValidationError(
            f"scope entry is missing required field '{key}'",
            {"path": str(path), "field": key},
        )
    return entry[key]


def _str_list(entry: dict[str, Any], key: str, path: Path) -> tuple[str, ...]:
    value = _require(entry, key, path)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError(
            f"scope field '{key}' must be a list of strings",
            {"path": str(path), "field": key, "value": value},
        )
    return tuple(value)


def _ms(entry: dict[str, Any], key: str, path: Path) -> int:
    value = _require(entry, key, path)
    if not isinstance(value, str):
        raise ValidationError(
            f"scope field '{key}' must be an ISO-8601 UTC timestamp",
            {"path": str(path), "field": key, "value": value},
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            f"scope field '{key}' is not a valid ISO-8601 timestamp",
            {"path": str(path), "field": key, "value": value},
        ) from exc
    if parsed.tzinfo is None:
        raise ValidationError(
            f"scope field '{key}' must carry an explicit UTC offset",
            {"path": str(path), "field": key, "value": value},
        )
    delta = parsed.astimezone(UTC) - _EPOCH
    return (delta.days * DAY_MS) + (delta.seconds * 1000) + (delta.microseconds // 1000)


def _parse_entry(family: str, entry: dict[str, Any], path: Path) -> ScopeBoundaries:
    span_days = _require(entry, "span_days", path)
    if not isinstance(span_days, int) or isinstance(span_days, bool):
        raise ValidationError(
            "scope field 'span_days' must be an integer",
            {"path": str(path), "field": "span_days", "value": span_days},
        )
    frozen_at = _require(entry, "frozen_at", path)
    frozen_by = _require(entry, "frozen_by", path)
    if not isinstance(frozen_at, str) or not isinstance(frozen_by, str):
        raise ValidationError(
            "scope fields 'frozen_at'/'frozen_by' must be strings",
            {"path": str(path), "frozen_at": frozen_at, "frozen_by": frozen_by},
        )
    boundaries = ScopeBoundaries(
        family=family,
        universe=_str_list(entry, "universe", path),
        timeframes=_str_list(entry, "timeframes", path),
        s_start=_ms(entry, "s_start", path),
        s_end=_ms(entry, "s_end", path),
        oos_start=_ms(entry, "oos_start", path),
        holdout_start=_ms(entry, "holdout_start", path),
        span_days=span_days,
        frozen_at=frozen_at,
        frozen_by=frozen_by,
    )
    if not boundaries.universe:
        raise ValidationError("scope 'universe' must not be empty", {"path": str(path)})
    if not (boundaries.s_start < boundaries.oos_start < boundaries.holdout_start < boundaries.s_end):
        raise ValidationError(
            "scope boundaries must satisfy s_start < oos_start < holdout_start < s_end",
            {
                "path": str(path),
                "family": family,
                "s_start": boundaries.s_start,
                "oos_start": boundaries.oos_start,
                "holdout_start": boundaries.holdout_start,
                "s_end": boundaries.s_end,
            },
        )
    return boundaries


def _dump_entry(boundaries: ScopeBoundaries) -> dict[str, Any]:
    return {
        "universe": list(boundaries.universe),
        "timeframes": list(boundaries.timeframes),
        "s_start": _to_iso(boundaries.s_start),
        "s_end": _to_iso(boundaries.s_end),
        "span_days": boundaries.span_days,
        "oos_start": _to_iso(boundaries.oos_start),
        "holdout_start": _to_iso(boundaries.holdout_start),
        "frozen_at": boundaries.frozen_at,
        "frozen_by": boundaries.frozen_by,
    }


def _to_iso(ms: int) -> str:
    return (_EPOCH + timedelta(milliseconds=ms)).strftime("%Y-%m-%dT%H:%M:%SZ")
