"""M14 — the absorption concept state (spec §17).

The §17 concept: "aggressive buying + failure to advance → potential sell
absorption".

This is a VISUALIZATION HEURISTIC, never a formal Event Engine detector
(D-094): "A visualization heuristic is not automatically a formal Event
Engine detector. Formal event logic requires ``specs/events/<id>.md`` under
PD-4." The rule is declared and deterministic; its provenance is
``CALCULATED``, and the docstring + a test enforce the warning.

Where actual taker-side data exists, it is used rather than estimating side
from candle direction (§17) - a bucket set without taker volumes is refused.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from engine.errors import ValidationError
from engine.visualization.microstructure import TradeBucket, _require_taker_side


@dataclass(frozen=True)
class AbsorptionSignal:
    """The §17 absorption state: aggressive buying that failed to advance.

    ``heuristic_only`` is always True: this is a visualization heuristic,
    not an Event Engine detector (D-094, PD-4).
    """

    ts: str
    aggressive_buy: float
    price_change: float
    heuristic_only: bool = True

    def as_dict(self) -> dict[str, str | float | bool]:
        return {
            "ts": self.ts,
            "aggressive_buy": self.aggressive_buy,
            "price_change": self.price_change,
            "provenance": "CALCULATED",
            "heuristic_only": self.heuristic_only,
        }


def absorption(
    buckets: Sequence[TradeBucket],
    *,
    advance_threshold: float = 0.0,
) -> tuple[AbsorptionSignal, ...]:
    """Detect the §17 concept state: aggressive buying + failure to advance.

    A bucket is a candidate when its taker buy volume exceeds its sell
    volume (aggressive buying) and the price failed to advance by more than
    ``advance_threshold`` (a declared threshold, never inferred).

    Refuses without exchange-side taker data (§17/D-092). Every returned
    signal carries ``heuristic_only=True`` - it is a visualization
    heuristic, never an Event Engine detector (PD-4).
    """
    _require_taker_side(buckets)
    if advance_threshold < 0:
        raise ValidationError(
            "advance_threshold must be non-negative", {"advance_threshold": advance_threshold}
        )
    out: list[AbsorptionSignal] = []
    for index, bucket in enumerate(buckets):
        buy = float(bucket.taker_buy_volume)  # type: ignore[arg-type]
        sell = float(bucket.taker_sell_volume)  # type: ignore[arg-type]
        if buy <= sell:
            continue  # not aggressive buying
        previous_price = buckets[index - 1].price if index > 0 else bucket.price
        movement = bucket.price - previous_price
        if movement > advance_threshold:
            continue  # the price advanced: not absorption
        out.append(
            AbsorptionSignal(
                ts=bucket.ts,
                aggressive_buy=buy - sell,
                price_change=movement,
            )
        )
    return tuple(out)
