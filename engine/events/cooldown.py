"""The parity-critical cooldown gate (spec section 6.7, design section 3).

Shared by every raw-signal stream. Implements Pine
``sig = raw and nz(barssince(raw)[1], 999) >= cooldown`` exactly:

- ``barssince_raw[t]`` = 0 where raw is true, else bars since the last true
  (None before the first true) - same as :func:`engine.features.primitives.barssince`;
- ``gap[t]`` lags by one bar: the current bar's ``barssince`` is not yet
  visible, so a raw bar never suppresses *itself*;
- ``gap`` seeds to 999 when the lag is None or the series starts, so the
  first-ever raw always emits.

State is **independent per event type** - the caller maintains one boolean
series per type and calls this function once per series; there is no shared
accumulator (fixtures A, F, G, D-022).
"""

from __future__ import annotations

from collections.abc import Sequence

from engine.features.primitives import barssince

#: Seed when no previous barssince exists (Pine ``nz(..., 999)``).
GAP_SEED = 999


def apply_cooldown(raw: Sequence[bool], cooldown: int) -> list[bool]:
    """Gate ``raw`` through the barssince cooldown; returns one bool per bar.

    ``barssince`` counts **raw** occurrences, not emitted signals (spec line
    265): a suppressed raw still resets the window, matching the Pine study
    where ``barssince(raw)`` watches the un-gated condition.
    """
    if cooldown < 0:
        raise ValueError(f"cooldown must be >= 0, got {cooldown}")
    if not raw:
        return []
    since = barssince(list(raw))
    out: list[bool] = [False] * len(raw)
    for index, is_raw in enumerate(raw):
        if not is_raw:
            continue
        previous = since[index - 1] if index > 0 else None
        gap = GAP_SEED if previous is None else previous
        out[index] = gap >= cooldown
    return out
