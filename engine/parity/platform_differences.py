"""Section 14.2 line 592: the documented platform-difference table.

Each entry carries the design-note anchor the spec requires for the
``EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE`` category. An entry *explains* a
difference; it never erases one - the difference still shows up in the report
with its numbers, and a human decides whether the explanation holds
(line 592: failure never automatically means the engine is wrong, and equally
never automatically TradingView is right).

A matcher judges one differing pair against a :class:`DifferenceContext`
carrying where the pair sits (engine index, OHLCV field or plot name, and the
rvol bars a documented volume difference contaminates), so an entry can be
narrow: field-specific, index-bounded and tolerance-bounded (design note 4.6).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from engine.parity.constants import WARMUP_BARS
from engine.parity.importers import significant_digits

#: Volume bound of ``tv_volume_feed``: the observed TradingView feed
#: deviation on the AC export was <= 1.9e-4 rel; 1e-3 is the documented
#: ceiling above which a volume difference stops being explained (4.6).
VOLUME_FEED_REL_TOL = 1e-3

#: Regime-EMA seed wash-out of ``ema_seed_history``: the observed residual
#: at the window start was 3.5e-6 rel, decaying below ``REL_TOL`` within
#: ~60 bars; the entry covers ``EMA_WASHOUT_BARS`` bars at up to 1e-5 (4.6).
EMA_WASHOUT_REL_TOL = 1e-5
EMA_WASHOUT_BARS = 100


@dataclass(frozen=True, slots=True)
class DifferenceContext:
    """Where a differing pair sits, so entries can stay narrow (4.6).

    ``engine_index`` is the index into the engine dataset, ``field`` the
    OHLCV field (``volume``, ``close``, ...) or plot name (``rvol``,
    ``ema_regime``, ...) the values came from, and ``rvol_contaminated``
    the engine bars whose rvol uses a volume the feeds disagreed on.
    """

    engine_index: int | None = None
    field: str | None = None
    rvol_contaminated: frozenset[int] = frozenset()


#: An entry decides whether one differing value pair is documented:
#: (reference value, engine value, reference bar index, raw exported text,
#: difference context).
DifferenceMatcher = Callable[[float | None, float | None, int, str, DifferenceContext], bool]


@dataclass(frozen=True, slots=True)
class PlatformDifference:
    """One documented TradingView/engine difference (spec line 592)."""

    name: str
    summary: str
    #: The design-note anchor line 592 requires on every entry.
    anchor: str
    matcher: DifferenceMatcher


def _relative(reference: float, engine: float) -> float:
    if reference == engine:
        return 0.0
    scale = max(abs(reference), abs(engine))
    return abs(reference - engine) / scale if scale else 0.0


def _export_rounding(
    reference: float | None,
    engine: float | None,
    bar_index: int,
    raw: str,
    context: DifferenceContext,
) -> bool:
    """The engine value rounded to the exported precision equals the export."""
    del bar_index, context
    if reference is None or engine is None:
        return False
    digits = significant_digits(raw)
    if digits is None:
        return False
    return float(f"{engine:.{digits}g}") == reference


def _na_inside_warmup(
    reference: float | None,
    engine: float | None,
    bar_index: int,
    raw: str,
    context: DifferenceContext,
) -> bool:
    """Pine ``na`` against a computed engine value, only inside the warm-up."""
    del raw, context
    return reference is None and engine is not None and bar_index < WARMUP_BARS


def _tv_volume_feed(
    reference: float | None,
    engine: float | None,
    bar_index: int,
    raw: str,
    context: DifferenceContext,
) -> bool:
    """TradingView's volume feed, and the rvol bars its deviation touches.

    The engine holds the exchange kline value verbatim (verified against
    ``api.binance.com`` on the AC export); TradingView's own aggregation
    differs on a few historical bars by up to 1.9e-4 rel. A differing
    ``volume`` field within ``VOLUME_FEED_REL_TOL`` is documented, and so is
    rvol on the bars whose 20-bar mean or numerator uses that volume, within
    the same bound. Any other plot or a wider difference is unexplained.
    """
    del bar_index, raw
    if reference is None or engine is None:
        return False
    relative = _relative(reference, engine)
    if relative > VOLUME_FEED_REL_TOL:
        return False
    if context.field == "volume":
        return True
    return context.field == "rvol" and context.engine_index in context.rvol_contaminated


def _ema_seed_history(
    reference: float | None,
    engine: float | None,
    bar_index: int,
    raw: str,
    context: DifferenceContext,
) -> bool:
    """Regime EMA seed wash-out while the shorter engine history converges.

    The engine's ``ta.ema(close, 100)`` seeds at the fixture's first bar
    (2024-01-01); TradingView seeds at the symbol's full history
    (2020-09-14). The seed weight decays as ``(1 - 2/101)^n``, so the
    residual is visible only in the first ``EMA_WASHOUT_BARS`` bars of the
    comparison window and within ``EMA_WASHOUT_REL_TOL`` (4.6).
    """
    del bar_index, raw
    if reference is None or engine is None:
        return False
    if context.field != "ema_regime" or context.engine_index is None:
        return False
    if not WARMUP_BARS <= context.engine_index < WARMUP_BARS + EMA_WASHOUT_BARS:
        return False
    return _relative(reference, engine) <= EMA_WASHOUT_REL_TOL


#: Design note 4.3/4.6 table. New entries need an anchor and a test.
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
    PlatformDifference(
        name="tv_volume_feed",
        summary=(
            "TradingView's volume aggregation differs from the exchange kline volume "
            "the engine holds (verified against api.binance.com) on a few historical "
            f"bars, at most {VOLUME_FEED_REL_TOL:g} rel, together with rvol on the "
            "bars whose 20-bar window uses that volume"
        ),
        anchor="specs/design/m3_features.md 4.6 (spec 14.2 line 592)",
        matcher=_tv_volume_feed,
    ),
    PlatformDifference(
        name="ema_seed_history",
        summary=(
            "the engine's regime EMA(100) seeds at the fixture's first bar while "
            "TradingView seeds at the symbol's full history; the wash-out residual "
            f"(<= {EMA_WASHOUT_REL_TOL:g} rel) is visible only in the first "
            f"{EMA_WASHOUT_BARS} bars of the comparison window"
        ),
        anchor="specs/design/m3_features.md 4.6 (spec 14.2 line 592)",
        matcher=_ema_seed_history,
    ),
)


def find_documented_difference(
    reference: float | None,
    engine: float | None,
    *,
    bar_index: int,
    raw: str,
    context: DifferenceContext | None = None,
) -> PlatformDifference | None:
    """First table entry explaining this difference, or ``None``."""
    where = context if context is not None else DifferenceContext()
    for entry in PLATFORM_DIFFERENCES:
        if entry.matcher(reference, engine, bar_index, raw, where):
            return entry
    return None
