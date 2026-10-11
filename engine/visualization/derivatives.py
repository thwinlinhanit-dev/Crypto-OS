"""M15 — the derivatives observed series: OI + funding (spec §16).

These are the EXCHANGE-OBSERVED inputs (provenance OBSERVED): the funding
series (positive rate = longs pay, the M10/D-070 convention) and the OI
series. The derived OI change % is a CALCULATED transformation of the
observed series. The funding accrual reuses the M10 ``funding_charges``
boundary convention (entry-exclusive, exit-inclusive) rather than a second
implementation (D-099).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from engine.errors import ValidationError


@dataclass(frozen=True)
class OiPoint:
    """One OI observation (ts, oi); provenance OBSERVED."""

    ts: str
    oi: float

    def as_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "oi": self.oi, "provenance": "OBSERVED"}


@dataclass(frozen=True)
class FundingPoint:
    """One funding observation (ts, rate); provenance OBSERVED.

    The M10/D-070 convention: a positive rate means longs pay shorts.
    """

    ts: str
    rate: float

    def as_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "rate": self.rate, "provenance": "OBSERVED"}


def oi_change_pct(points: Sequence[OiPoint]) -> tuple[tuple[str, float], ...]:
    """The per-bar OI change % (a CALCULATED transformation of the observed
    series). The first bar has no prior, so it is dropped (never a
    fabricated 0.0)."""
    if len(points) < 2:
        return ()
    out: list[tuple[str, float]] = []
    for previous, current in pairwise(points):
        if previous.oi == 0:
            raise ValidationError(
                "a zero prior OI makes the change percentage undefined",
                {"ts": current.ts},
            )
        out.append((current.ts, (current.oi - previous.oi) / previous.oi * 100.0))
    return tuple(out)


def render_oi(points: Sequence[OiPoint]) -> dict[str, Any]:
    """The deterministic OI render graph (the §13 line primitive)."""
    if not points:
        raise ValidationError("an OI render needs at least one point", {})
    return {
        "kind": "LINE",
        "label": "Open Interest",
        "provenance": "OBSERVED",
        "points": [[p.ts, p.oi] for p in points],
    }


def funding_series(
    points: Sequence[FundingPoint],
    *,
    side: str,
) -> tuple[tuple[str, float], ...]:
    """The cumulative funding accrual for a declared position (spec §16).

    Reuses the M10/D-070 convention: a positive rate means longs pay. A
    long's accrual is ``+rate``; a short's is ``-rate``. The boundary
    convention (entry-exclusive, exit-inclusive) is the M10
    ``funding_charges`` contract applied to the whole series.
    """
    normalized = side.strip().upper()
    if normalized not in ("LONG", "SHORT"):
        raise ValidationError(
            "side must be LONG or SHORT", {"side": side}
        )
    sign = 1.0 if normalized == "LONG" else -1.0
    out: list[tuple[str, float]] = []
    running = 0.0
    for point in points:
        running += sign * point.rate
        out.append((point.ts, running))
    return tuple(out)


def render_funding(
    points: Sequence[FundingPoint],
    *,
    side: str = "LONG",
) -> dict[str, Any]:
    """The deterministic funding render graph (the cumulative accrual)."""
    if not points:
        raise ValidationError("a funding render needs at least one point", {})
    series = funding_series(points, side=side)
    return {
        "kind": "LINE",
        "label": f"Funding accrual ({side})",
        "provenance": "CALCULATED",
        "points": [[ts, value] for ts, value in series],
    }
