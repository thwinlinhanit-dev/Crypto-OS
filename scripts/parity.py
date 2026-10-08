"""M3 T3.2 - run the section 14.2 layer-1 parity comparison.

Usage: ``uv run python scripts/parity.py`` (or ``make parity``).
Imports the owner's TradingView chart-data export, compares it against the
engine over the fixture dataset (no network, PD-10), writes
``reports/parity/layer1_report.json`` and exits 0 only for a compared run that
matched (EXACT / EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE, D-019).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from apps.api.db import connection_scope  # noqa: E402  (path bootstrap above)
from engine.data.bars import Timeframe  # noqa: E402
from engine.data.instruments import MarketType, instrument_id_for  # noqa: E402
from engine.data.seed import FIXTURES_ROOT, load_fixture  # noqa: E402
from engine.errors import DataUnavailableError, ValidationError  # noqa: E402
from engine.parity import ParityRun, run_parity  # noqa: E402
from engine.versions import DATASET_SCHEMA_VERSION, ENGINE_VERSION, code_version  # noqa: E402

#: The layer-1 run compares BTCUSDT daily (spec 14.2 line 590 fixtures).
SYMBOL = "BTCUSDT"
TIMEFRAME = Timeframe.D1

DEFAULT_CSV = ROOT / "fixtures" / "parity" / f"BINANCE_{SYMBOL}_{TIMEFRAME.value}.csv"
DEFAULT_META = ROOT / "fixtures" / "parity" / f"BINANCE_{SYMBOL}_{TIMEFRAME.value}.meta.json"
DEFAULT_REPORT = ROOT / "reports" / "parity" / "layer1_report.json"


def _load_provenance() -> tuple[dict[str, Any] | None, list[str]]:
    """PD-6 identity for this run's engine data, read from the manifest.

    The manifest lives in the database (it hashes the parquet parts, not the
    fixture JSON), so an unavailable or ambiguous database yields no provenance
    rather than an assumed dataset_version - the run then classifies UNKNOWN.
    """
    warnings: list[str] = []
    instrument = instrument_id_for("BINANCE", MarketType.SPOT, SYMBOL)
    try:
        with connection_scope() as conn:
            row = conn.execute(
                text(
                    "SELECT dataset_version FROM market.dataset_manifest"
                    " WHERE instruments @> CAST(:instruments AS jsonb)"
                    " AND timeframes @> CAST(:timeframes AS jsonb)"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {
                    "instruments": json.dumps([str(instrument)]),
                    "timeframes": json.dumps([TIMEFRAME.value]),
                },
            ).first()
    except Exception as exc:  # fail closed, never invent a version (PD-6)
        warnings.append(f"dataset provenance unavailable (PD-6): {exc}")
        return None, warnings
    if row is None:
        warnings.append(
            "no registered dataset covers BTCUSDT 1D - run `make seed-fixtures` first (PD-6)"
        )
        return None, warnings
    return {
        "dataset_version": str(row.dataset_version),
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "code_version": code_version(),
        "seed": None,
    }, warnings


def _print_table(run: ParityRun) -> None:
    report = run.report
    if report is None:
        print(run.message)
        return
    print(f"{'plot':<14}{'status':<46}{'compared':>9}{'matched':>9}{'doc':>6}{'unexp':>7}")
    for name, plot in report.plots.items():
        print(
            f"{name:<14}{plot.status.value:<46}{plot.compared:>9}{plot.matched:>9}"
            f"{plot.documented:>6}{plot.unexplained:>7}"
        )
    for warning in report.warnings:
        print(f"warning: {warning}")
    print(f"overall: {report.status.value}  pass(spec)={report.passed}")
    print(run.message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CryptoOS parity layer 1 (spec 14.2)")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="chart-data export path")
    parser.add_argument("--meta", type=Path, default=DEFAULT_META, help="run-metadata sidecar")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="report output path")
    args = parser.parse_args(argv)

    try:
        fixture = load_fixture(FIXTURES_ROOT, SYMBOL, TIMEFRAME)
    except (DataUnavailableError, ValidationError) as exc:
        print(f"engine data unavailable: {exc}", file=sys.stderr)
        return 1

    provenance, warnings = _load_provenance()
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    run = run_parity(
        csv_path=args.csv,
        meta_path=args.meta,
        report_path=args.report,
        engine_bars=fixture.klines,
        provenance=provenance,
    )
    _print_table(run)
    return run.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
