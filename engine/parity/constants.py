"""Section 14.2 layer-1 comparison protocol constants (spec lines 590, 592)."""

from __future__ import annotations

#: Spec line 590: compare after the protocol warm-up - skip the first 500 bars.
WARMUP_BARS = 500

#: Spec line 590: relative tolerance for every compared value
#: (``|a - b| <= REL_TOL * max(|a|, |b|)``).
REL_TOL = 1e-6
