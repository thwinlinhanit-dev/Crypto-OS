"""M10 — the funding cost model (spec T10.3/7.7, design note m10_perp_live
section 4, D-070).

Funding accrues every 8h while a position is open; the cost model charges
the position's notional at the funding rate — a **positive** rate means
longs pay shorts (the venue convention, spec 7.7). The charge is deducted
from the trade's realized PnL and recorded per trade (D-041: append-only,
part of the experiment's lineage).

The funding schedule is a sorted sequence of ``(timestamp, rate)``; the
model is a pure function of the schedule, the position window and the
notional, so the M6 simulator can call it per bar without any DB access.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from engine.exchanges.perp import FUNDING_INTERVAL_HOURS

#: One funding interval in milliseconds (8h).
FUNDING_INTERVAL_MS: Final = FUNDING_INTERVAL_HOURS * 3_600_000


@dataclass(frozen=True)
class FundingCharge:
    """One funding accrual inside the position window.

    ``rate`` is the venue's funding rate; ``paid`` is the amount charged
    **to the position** (positive = the position pays, e.g. a long at a
    positive rate; negative = the position receives, a long at a negative
    rate / a short at a positive rate).
    """

    ts: int
    rate: float
    notional: float
    paid: float


@dataclass(frozen=True)
class FundingResult:
    """The total funding effect over a position window.

    ``total_paid`` is the net charge (positive = the position paid, i.e.
    it reduces realized PnL); ``charges`` are the individual accruals.
    """

    total_paid: float
    charges: list[FundingCharge]


def funding_charges(
    *,
    side: str,
    entry_ms: int,
    exit_ms: int,
    notional: float,
    schedule: Sequence[tuple[int, float]],
) -> list[FundingCharge]:
    """The funding accruals inside ``[entry_ms, exit_ms]``.

    A funding event at exactly ``entry_ms`` is NOT charged (the position
    opens at that instant); an event at exactly ``exit_ms`` IS charged
    (the position was open through it). A long pays at a positive rate; a
    short pays at a negative rate (the venue convention, D-070).
    """
    if exit_ms <= entry_ms:
        return []
    normalized = side.strip().upper()
    if normalized not in ("LONG", "SHORT"):
        raise ValueError(f"side must be LONG or SHORT, got {side!r}")
    sign = 1.0 if normalized == "LONG" else -1.0
    charges: list[FundingCharge] = []
    for ts, rate in sorted(schedule):
        if ts <= entry_ms or ts > exit_ms:
            continue
        paid = sign * rate * notional
        charges.append(
            FundingCharge(ts=ts, rate=rate, notional=notional, paid=paid)
        )
    return charges


def funding_cost(
    *,
    side: str,
    entry_ms: int,
    exit_ms: int,
    notional: float,
    schedule: Sequence[tuple[int, float]],
) -> FundingResult:
    """The net funding charge over the window (positive = the position
    paid; the caller subtracts it from realized PnL)."""
    charges = funding_charges(
        side=side,
        entry_ms=entry_ms,
        exit_ms=exit_ms,
        notional=notional,
        schedule=schedule,
    )
    return FundingResult(
        total_paid=sum(charge.paid for charge in charges),
        charges=charges,
    )


def next_funding_ts(ts: int) -> int:
    """The next funding timestamp at or after ``ts`` (8h grid from the
    venue epoch)."""
    epoch = 0
    remainder = (ts - epoch) % FUNDING_INTERVAL_MS
    return ts if remainder == 0 else ts + (FUNDING_INTERVAL_MS - remainder)
