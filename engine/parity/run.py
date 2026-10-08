"""Parity orchestration: one export, one engine dataset, one report (design note 4.4).

The exit rule (D-019, design note 4.5): the report carries the spec's own
``pass`` (zero ENGINE_BUG, zero UNKNOWN - spec line 592), while the process
exits 0 only for ``EXACT`` or ``EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE``.
``DATA_MISMATCH`` must never read green in ``make parity`` even though spec
line 592 does not classify it as an engine bug: nothing was compared.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.errors import ValidationError
from engine.parity.compare import ParityReport, compare_layer1, unknown_report
from engine.parity.importers import import_chart_csv
from engine.parity.metadata import load_metadata
from engine.parity.status import ParityStatus
from exchanges.base import Kline

#: Exit codes: 0 compared and equal, 1 anything else (never green on failure).
EXIT_PASS = 0
EXIT_FAIL = 1

#: The only statuses ``make parity`` counts as success (D-019).
GATE_PASSES: frozenset[ParityStatus] = frozenset(
    {ParityStatus.EXACT, ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE}
)


@dataclass(frozen=True, slots=True)
class ParityRun:
    """Outcome of one parity invocation: report (if any), message, exit code."""

    report: ParityReport | None
    message: str
    exit_code: int


def run_parity(
    *,
    csv_path: Path,
    meta_path: Path,
    report_path: Path,
    engine_bars: Sequence[Kline],
    provenance: Mapping[str, Any] | None,
) -> ParityRun:
    """Import, compare, write the report; classify every failure mode (4.2)."""
    if not csv_path.is_file():
        return ParityRun(
            report=None,
            message=(
                "NO_REFERENCE_EXPORT: no TradingView chart-data export at "
                f"{csv_path.as_posix()} - export the script's chart data "
                "(four plots + OHLCV) to that path with its .meta.json sidecar"
            ),
            exit_code=EXIT_FAIL,
        )

    reference = None
    metadata = None
    try:
        reference = import_chart_csv(csv_path)
        metadata = load_metadata(meta_path)
    except ValidationError as exc:
        report = unknown_report(
            f"import failed: {exc}",
            reference={"source": csv_path.as_posix()},
            metadata=None,
            provenance=provenance,
        )
        report.write(report_path)
        return ParityRun(
            report=report,
            message=f"UNKNOWN: {exc} (report: {report_path.as_posix()})",
            exit_code=EXIT_FAIL,
        )

    if provenance is None:
        report = unknown_report(
            "engine provenance unavailable (PD-6)",
            reference={"source": reference.source, "bars": len(reference.bars)},
            metadata=metadata,
            provenance=None,
        )
        report.write(report_path)
        return ParityRun(
            report=report,
            message=f"UNKNOWN: engine provenance unavailable (report: {report_path.as_posix()})",
            exit_code=EXIT_FAIL,
        )

    report = compare_layer1(
        reference,
        engine_bars=engine_bars,
        metadata=metadata,
        provenance=provenance,
    )
    report.write(report_path)
    exit_code = EXIT_PASS if report.status in GATE_PASSES else EXIT_FAIL
    message = (
        f"{report.status.value}: report written to {report_path.as_posix()}"
        if report.reason is None
        else f"{report.status.value}: {report.reason} (report: {report_path.as_posix()})"
    )
    return ParityRun(report=report, message=message, exit_code=exit_code)
