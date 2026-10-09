"""Parity layer 1 (spec section 14.2, milestone M3).

Public surface:
- :func:`import_chart_csv` / :func:`load_metadata` - import the TradingView export.
- :func:`compare_layer1` - align, compare OHLCV then the four plots, classify.
- :func:`run_parity` - one invocation end to end (report file + exit code).
- :class:`ParityStatus` - the spec line 592 vocabulary.
"""

from __future__ import annotations

from engine.parity.compare import (
    PLOT_SPECS,
    ParityReport,
    PlotResult,
    compare_layer1,
    engine_frame,
)
from engine.parity.constants import REL_TOL, WARMUP_BARS
from engine.parity.importers import (
    ReferenceBar,
    ReferenceExport,
    import_chart_csv,
    parse_time_ms,
    significant_digits,
)
from engine.parity.metadata import (
    EXPECTED_BAR_MAGNIFIER,
    REQUIRED_FIELDS,
    TradingViewRunMetadata,
    load_metadata,
)
from engine.parity.platform_differences import (
    PLATFORM_DIFFERENCES,
    DifferenceContext,
    PlatformDifference,
)
from engine.parity.run import EXIT_FAIL, EXIT_PASS, GATE_PASSES, ParityRun, run_parity
from engine.parity.status import ParityStatus, is_pass, overall, worst_of

__all__ = [
    "EXIT_FAIL",
    "EXIT_PASS",
    "EXPECTED_BAR_MAGNIFIER",
    "GATE_PASSES",
    "PLATFORM_DIFFERENCES",
    "PLOT_SPECS",
    "REL_TOL",
    "REQUIRED_FIELDS",
    "WARMUP_BARS",
    "DifferenceContext",
    "ParityReport",
    "ParityRun",
    "ParityStatus",
    "PlatformDifference",
    "PlotResult",
    "ReferenceBar",
    "ReferenceExport",
    "TradingViewRunMetadata",
    "compare_layer1",
    "engine_frame",
    "import_chart_csv",
    "is_pass",
    "load_metadata",
    "overall",
    "parse_time_ms",
    "run_parity",
    "significant_digits",
    "worst_of",
]
