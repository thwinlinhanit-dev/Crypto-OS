"""Section 14.2 line 592: the documented platform-difference table.

Each entry carries the design-note anchor the spec requires for the
``EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE`` category. An entry *explains* a
difference; it never erases one - the difference still shows up in the report
with its numbers, and a human decides whether the explanation holds
(line 592: failure never automatically means the engine is wrong, and equally
never automatically TradingView is right).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from engine.parity.constants import WARMUP_BARS
from engine.parity.importers import significant_digits

#: An entry decides whether one differing value pair is documented:
#: (reference value, engine value, reference bar index, raw exported text).
DifferenceMatcher = Callable[[float | None, float | None, int, str], bool]


@dataclass(frozen=True, slots=True)
class PlatformDifference:
    """One documented TradingView/engine difference (spec line 592)."""

    name: str
    summary: str
    #: The design-note anchor line 592 requires on every entry.
    anchor: str
    matcher: DifferenceMatcher


def _export_rounding(
    reference: float | None, engine: float | None, bar_index: int, raw: str
) -> bool:
    """The engine value rounded to the exported precision equals the export."""
    del bar_index
    if reference is None or engine is None:
        return False
    digits = significant_digits(raw)
    if digits is None:
        return False
    return float(f"{engine:.{digits}g}") == reference


def _na_inside_warmup(
    reference: float | None, engine: float | None, bar_index: int, raw: str
) -> bool:
    """Pine ``na`` against a computed engine value, only inside the warm-up."""
    del raw
    return reference is None and engine is not None and bar_index < WARMUP_BARS


#: Design note 4.3 initial table. New entries need an anchor and a test.
PLATFORM_DIFFERENCES: tuple[PlatformDifference, ...] = (
    PlatformDifference(
        name="tv_export_rounding",
        summary=(
            "TradingView writes ~6-8 significant digits, so a value that differs only "
            "beyond the exported precision was rounded on export"
        ),
        anchor="specs/design/m3_features.md 4.3 (spec 14.2 line 592)",
        matcher=_export_rounding,
    ),
    PlatformDifference(
        name="tv_na_vs_engine_null",
        summary=(
            "`na` in the export against a computed engine value at the same bar, "
            "only inside the first 500 warm-up bars"
        ),
        anchor="specs/design/m3_features.md 4.3 (spec 14.2 line 592)",
        matcher=_na_inside_warmup,
    ),
)


def find_documented_difference(
    reference: float | None,
    engine: float | None,
    *,
    bar_index: int,
    raw: str,
) -> PlatformDifference | None:
    """First table entry explaining this difference, or ``None``."""
    for entry in PLATFORM_DIFFERENCES:
        if entry.matcher(reference, engine, bar_index, raw):
            return entry
    return None
