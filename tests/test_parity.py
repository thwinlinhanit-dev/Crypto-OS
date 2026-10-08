"""M3 (T3.2) - section 14.2 layer-1 parity tool.

Acceptance evidence produced here:
- the CSV importer accepts the documented header aliases, epoch/ISO/dot times
  and the ``na`` plot tokens, and rejects every malformed export (unknown or
  missing column, unparsable time, null OHLCV, ragged row, non-increasing
  time, empty file) instead of coercing (PD-10, design note 4.2);
- run metadata with a missing field or sidecar fails closed (PD-11);
- the classification order of design note 4.3: OHLCV mismatch ->
  DATA_MISMATCH with a bar diff report; engine failure -> ENGINE_BUG;
  unexplained plot differences -> ENGINE_BUG; rounded export values ->
  EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE; < 501 comparable bars ->
  UNKNOWN (spec line 590's skip-the-first-500 rule);
- the gate (D-019): exit 0 only for EXACT / EXACT_WITH_DOCUMENTED_PLATFORM_
  DIFFERENCE, exit 1 for DATA_MISMATCH, ENGINE_BUG, UNKNOWN and a missing
  export (``NO_REFERENCE_EXPORT``);
- reports embed PD-6 provenance and are byte-identical across reruns.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest

from engine.errors import ValidationError
from engine.parity import (
    REL_TOL,
    WARMUP_BARS,
    ParityStatus,
    TradingViewRunMetadata,
    compare_layer1,
    engine_frame,
    import_chart_csv,
    is_pass,
    load_metadata,
    overall,
    parse_time_ms,
    run_parity,
    significant_digits,
)
from engine.parity.compare import PLOT_SPECS, PlotSpec, compute_plots
from engine.parity.importers import NA_TOKENS, REQUIRED_PLOTS
from engine.parity.metadata import EXPECTED_BAR_MAGNIFIER, REQUIRED_FIELDS
from engine.parity.platform_differences import PLATFORM_DIFFERENCES, find_documented_difference
from engine.parity.run import EXIT_FAIL, EXIT_PASS
from exchanges.base import Kline
from tests.support import DAY, T0

BAR_COUNT = WARMUP_BARS + 120  # 620 bars: 500 warm-up + 120 compared
HEADER = [
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "rvol",
    "cpos",
    "upper wick share",
    "regime ema",
]

PROVENANCE: dict[str, Any] = {
    "dataset_version": "9f2c0a1b3c4d5e6f",
    "dataset_schema_version": "1",
    "engine_version": "0.1.0",
    "code_version": "testcode",
    "seed": None,
}

METADATA_DOCUMENT: dict[str, str] = {
    "script_version": "5.0",
    "symbol": "BINANCE:BTCUSDT",
    "timeframe": "1D",
    "date_range": "2024-01-01..2026-10-08",
    "bar_magnifier": EXPECTED_BAR_MAGNIFIER,
    "commission": "0.1%",
    "slippage": "1 tick",
    "fill_assumptions": "next bar open",
    "export_date": "2026-10-08",
}


def _bars(count: int) -> list[Kline]:
    """Deterministic daily candles with varying volume (rvol varies too)."""
    out: list[Kline] = []
    for index in range(count):
        close = 100.0 + 0.5 * index + (index % 7) * 0.25
        out.append(
            Kline(
                open_time=T0 + index * DAY,
                close_time=T0 + (index + 1) * DAY,
                open=close - 0.3,
                high=close + 1.1,
                low=close - 0.9,
                close=close,
                volume=1000.0 + (index % 13) * 37.5,
                quote_volume=close * 1000.0,
                trades=500 + index,
            )
        )
    return out


def _write_export(
    path: Path,
    bars: list[Kline],
    series: dict[str, list[float | None]],
    *,
    skip: set[int] | None = None,
    extra_before: int = 0,
    extra_after: int = 0,
    ema_override: dict[int, str] | None = None,
    close_scale: dict[int, float] | None = None,
) -> None:
    """Write a chart-data export whose plot columns come from the engine."""
    rows: list[list[str]] = []
    for _ in range(extra_before):
        first = bars[0]
        rows.append(
            [str(first.open_time - 3 * DAY), "100", "101", "99", "100", "10", "1", "0.5", "0.5", "100"]
        )
    for index, bar in enumerate(bars):
        if skip and index in skip:
            continue
        close = bar.close * (close_scale or {}).get(index, 1.0)
        cells = [str(bar.open_time)]
        cells += [f"{value:.12g}" for value in (bar.open, bar.high, bar.low, close, bar.volume)]
        for name in REQUIRED_PLOTS:
            if name == "ema_regime" and ema_override and index in ema_override:
                cells.append(ema_override[index])
                continue
            value = series[name][index]
            cells.append("#" if value is None else f"{value:.12g}")
        rows.append(cells)
    for _ in range(extra_after):
        last = bars[-1]
        rows.append(
            [str(last.open_time + 3 * DAY), "100", "101", "99", "100", "10", "1", "0.5", "0.5", "100"]
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8")


def _engine(bars: list[Any]) -> dict[str, list[float | None]]:
    series, errors, _ = compute_plots(engine_frame(bars))
    assert not any(error for error in errors.values()), errors
    return series


def _metadata(path: Path, **overrides: str) -> Path:
    document = {**METADATA_DOCUMENT, **overrides}
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


@pytest.fixture()
def scene(tmp_path: Path) -> dict[str, Any]:
    """One EXACT-shaped scene: 620 bars, export + sidecar written from engine."""
    bars = _bars(BAR_COUNT)
    series = _engine(bars)
    csv_path = tmp_path / "export.csv"
    _write_export(csv_path, bars, series)
    meta_path = _metadata(tmp_path / "export.meta.json")
    return {
        "bars": bars,
        "series": series,
        "csv": csv_path,
        "meta": meta_path,
        "report": tmp_path / "report" / "layer1_report.json",
    }


def _compare(scene: dict[str, Any]) -> Any:
    return compare_layer1(
        import_chart_csv(scene["csv"]),
        engine_bars=scene["bars"],
        metadata=load_metadata(scene["meta"]),
        provenance=PROVENANCE,
    )


# --------------------------------------------------------------------------
# importer
# --------------------------------------------------------------------------


def test_import_accepts_aliases_epoch_times_and_na(tmp_path: Path) -> None:
    path = tmp_path / "export.csv"
    path.write_text(
        "Date,Open,High,Low,Close,Volume,RVOL,Close position,Upper wick,Regime EMA\n"
        "1704067200,100,101,99,100.5,1000,1.5,0.42,0.5,100.25\n"
        "1704153600,100.5,102,100,101,1100,#,n/a,,NaN\n",
        encoding="utf-8",
    )
    export = import_chart_csv(path)
    assert len(export.bars) == 2
    first, second = export.bars
    assert first.time_ms == 1_704_067_200_000
    assert first.plots == {
        "rvol": 1.5,
        "cpos": 0.42,
        "upper_wick": 0.5,
        "ema_regime": 100.25,
    }
    assert second.plots == {
        "rvol": None,
        "cpos": None,
        "upper_wick": None,
        "ema_regime": None,
    }
    assert second.close == 101.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1704067200", 1_704_067_200_000),
        ("1704067200000", 1_704_067_200_000),
        ("2024-01-01T00:00:00Z", 1_704_067_200_000),
        ("2024.01.01 00:00", 1_704_067_200_000),
        ("2024-01-01", 1_704_067_200_000),
    ],
)
def test_parse_time_formats(text: str, expected: int) -> None:
    assert parse_time_ms(text) == expected


def test_significant_digits() -> None:
    assert significant_digits("400.1") == 4
    assert significant_digits("0.00120") == 3
    assert significant_digits("-2.5") == 2
    assert significant_digits("1e-5") == 1
    assert significant_digits("NaN") is None
    assert significant_digits("  ") is None


@pytest.mark.parametrize(
    "body",
    [
        # unknown column
        "time,open,high,low,close,volume,rvol,cpos,upper wick share,regime ema,turnover\n"
        "1704067200,1,1,1,1,1,1,1,1,1,5\n",
        # missing required column (volume)
        "time,open,high,low,close,rvol,cpos,upper wick share,regime ema\n"
        "1704067200,1,1,1,1,1,1,1,1\n",
        # duplicate column
        "time,open,high,low,close,volume,rvol,cpos,upper wick share,regime ema,RVOL\n"
        "1704067200,1,1,1,1,1,1,1,1,1,1\n",
        # unparsable time
        "time,open,high,low,close,volume,rvol,cpos,upper wick share,regime ema\n"
        "yesterday,1,1,1,1,1,1,1,1,1\n",
        # null in OHLCV
        "time,open,high,low,close,volume,rvol,cpos,upper wick share,regime ema\n"
        "1704067200,1,1,1,#,1,1,1,1,1\n",
        # ragged row
        "time,open,high,low,close,volume,rvol,cpos,upper wick share,regime ema\n"
        "1704067200,1,1,1,1,1,1,1,1,1,1\n",
        # non-increasing time
        "time,open,high,low,close,volume,rvol,cpos,upper wick share,regime ema\n"
        "1704067200,1,1,1,1,1,1,1,1,1\n"
        "1704067200,1,1,1,1,1,1,1,1,1\n",
        # header only
        "time,open,high,low,close,volume,rvol,cpos,upper wick share,regime ema\n",
    ],
)
def test_import_rejects_malformed_exports(tmp_path: Path, body: str) -> None:
    path = tmp_path / "export.csv"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValidationError):
        import_chart_csv(path)


def test_import_rejects_empty_and_missing_files(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValidationError, match="empty"):
        import_chart_csv(empty)
    with pytest.raises(ValidationError, match="not found"):
        import_chart_csv(tmp_path / "absent.csv")


def test_na_tokens_are_the_documented_four() -> None:
    assert {"#", "", "nan", "n/a"} == NA_TOKENS


# --------------------------------------------------------------------------
# run metadata (spec line 593)
# --------------------------------------------------------------------------


def test_metadata_requires_every_field(tmp_path: Path) -> None:
    incomplete = {key: value for key, value in METADATA_DOCUMENT.items() if key != "slippage"}
    with pytest.raises(ValidationError, match="incomplete") as excinfo:
        TradingViewRunMetadata.from_document(incomplete)
    assert excinfo.value.detail["missing"] == ["slippage"]

    sidecar = tmp_path / "meta.json"
    sidecar.write_text(json.dumps(incomplete), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_metadata(sidecar)
    with pytest.raises(ValidationError, match="missing"):
        load_metadata(tmp_path / "absent.json")


def test_metadata_round_trips_the_sidecar(tmp_path: Path) -> None:
    sidecar = _metadata(tmp_path / "meta.json", bar_magnifier="ON")
    metadata = load_metadata(sidecar)
    assert metadata.bar_magnifier == "ON"
    assert metadata.to_document() == {**METADATA_DOCUMENT, "bar_magnifier": "ON"}
    assert len(REQUIRED_FIELDS) == 9


# --------------------------------------------------------------------------
# status vocabulary (spec line 592)
# --------------------------------------------------------------------------


def test_status_vocabulary_and_pass_rule() -> None:
    assert len(ParityStatus) == 5
    assert is_pass(ParityStatus.EXACT)
    assert is_pass(ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE)
    assert is_pass(ParityStatus.DATA_MISMATCH)  # spec 592: not an engine bug
    assert not is_pass(ParityStatus.ENGINE_BUG)
    assert not is_pass(ParityStatus.UNKNOWN)


def test_overall_is_worst_wins_and_empty_is_unknown() -> None:
    assert overall({}) is ParityStatus.UNKNOWN
    assert overall({"a": ParityStatus.EXACT, "b": ParityStatus.ENGINE_BUG}) is (
        ParityStatus.ENGINE_BUG
    )
    assert overall(
        {"a": ParityStatus.DATA_MISMATCH, "b": ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE}
    ) is ParityStatus.DATA_MISMATCH
    assert overall({"a": ParityStatus.EXACT, "b": ParityStatus.EXACT}) is ParityStatus.EXACT


# --------------------------------------------------------------------------
# documented platform differences (spec line 592 anchors)
# --------------------------------------------------------------------------


def test_every_platform_difference_carries_an_anchor() -> None:
    assert len(PLATFORM_DIFFERENCES) >= 2
    for entry in PLATFORM_DIFFERENCES:
        assert entry.anchor, entry.name
        assert "specs/design/m3_features.md" in entry.anchor


def test_rounding_entry_matches_a_rounded_export_only() -> None:
    engine = 400.123_456_7
    rounded = find_documented_difference(
        400.1, engine, bar_index=600, raw="400.1"
    )
    assert rounded is not None and rounded.name == "tv_export_rounding"
    assert (
        find_documented_difference(399.0, engine, bar_index=600, raw="399.0") is None
    )


def test_na_entry_only_inside_the_warmup() -> None:
    inside = find_documented_difference(
        None, 1.0, bar_index=WARMUP_BARS - 1, raw="#"
    )
    assert inside is not None and inside.name == "tv_na_vs_engine_null"
    assert (
        find_documented_difference(None, 1.0, bar_index=WARMUP_BARS, raw="#") is None
    )
    assert find_documented_difference(None, None, bar_index=0, raw="#") is None


# --------------------------------------------------------------------------
# compare_layer1
# --------------------------------------------------------------------------


def test_identical_export_is_exact(scene: dict[str, Any]) -> None:
    report = _compare(scene)
    assert report.status is ParityStatus.EXACT
    assert report.passed is True
    assert report.reason is None
    assert set(report.plots) == set(PLOT_SPECS)
    for plot in report.plots.values():
        assert plot.status is ParityStatus.EXACT
        assert plot.compared == BAR_COUNT - WARMUP_BARS
        assert plot.matched == plot.compared
        assert plot.unexplained == 0
    assert report.reference["compared_rows"] == BAR_COUNT - WARMUP_BARS
    assert report.warmup is not None
    assert report.warmup["rows"] == WARMUP_BARS
    assert report.bar_diff is None
    engine_doc = report.provenance
    for field in ("dataset_version", "dataset_schema_version", "engine_version", "code_version"):
        assert engine_doc[field] == PROVENANCE[field]
    assert set(engine_doc["resolved_params"]) == set(PLOT_SPECS)


def test_report_is_deterministic_and_timestamp_free(scene: dict[str, Any]) -> None:
    first = _compare(scene).to_document()
    second = _compare(scene).to_document()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert "generated_at" not in first
    assert first["kind"] == "parity_layer1"
    assert first["pass"] is True


def test_rounded_export_value_is_documented(scene: dict[str, Any]) -> None:
    index = 600
    engine_value = scene["series"]["ema_regime"][index]
    assert engine_value is not None
    _write_export(
        scene["csv"],
        scene["bars"],
        scene["series"],
        ema_override={index: f"{engine_value:.4g}"},
    )
    report = _compare(scene)
    assert report.status is ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE
    assert report.passed is True  # spec 592: no ENGINE_BUG, no UNKNOWN
    plot = report.plots["ema_regime"]
    assert plot.status is ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE
    assert plot.documented == 1
    assert plot.unexplained == 0
    assert plot.max_rel_diff is not None and plot.max_rel_diff > REL_TOL


def test_unexplained_difference_is_engine_bug(scene: dict[str, Any]) -> None:
    index = 600
    engine_value = scene["series"]["ema_regime"][index]
    assert engine_value is not None
    _write_export(
        scene["csv"],
        scene["bars"],
        scene["series"],
        ema_override={index: f"{engine_value * 1.01:.12g}"},
    )
    report = _compare(scene)
    assert report.status is ParityStatus.ENGINE_BUG
    assert report.passed is False
    plot = report.plots["ema_regime"]
    assert plot.unexplained == 1
    assert plot.samples and plot.samples[0]["rel_diff"] == pytest.approx(0.01 / 1.01)


def test_engine_failure_is_engine_bug(scene: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        PLOT_SPECS, "rvol", PlotSpec("bogus_indicator", {}, "bogus", "test-anchor")
    )
    report = _compare(scene)
    assert report.status is ParityStatus.ENGINE_BUG
    plot = report.plots["rvol"]
    assert plot.status is ParityStatus.ENGINE_BUG
    assert plot.detail and "bogus_indicator" in plot.detail
    assert report.plots["cpos"].status is ParityStatus.EXACT


def test_ohlcv_mismatch_short_circuits_with_bar_diff(scene: dict[str, Any]) -> None:
    _write_export(scene["csv"], scene["bars"], scene["series"], close_scale={610: 1.01})
    report = _compare(scene)
    assert report.status is ParityStatus.DATA_MISMATCH
    assert report.bar_diff is not None
    assert report.bar_diff["count"] >= 1
    assert report.bar_diff["samples"][0]["field"] == "close"
    assert report.bar_diff["samples"][0]["rel_diff"] > REL_TOL
    assert all(plot.status is ParityStatus.DATA_MISMATCH for plot in report.plots.values())


def test_bar_diff_report_is_capped_at_twenty(scene: dict[str, Any]) -> None:
    scale = dict.fromkeys(range(590, BAR_COUNT), 1.01)
    _write_export(scene["csv"], scene["bars"], scene["series"], close_scale=scale)
    report = _compare(scene)
    assert report.bar_diff is not None
    assert report.bar_diff["count"] == 30
    assert len(report.bar_diff["samples"]) == 20


def test_missing_reference_bar_inside_range_is_mismatch(scene: dict[str, Any]) -> None:
    _write_export(scene["csv"], scene["bars"], scene["series"], skip={550})
    report = _compare(scene)
    assert report.status is ParityStatus.DATA_MISMATCH
    assert report.bar_diff is not None
    assert report.bar_diff["samples"][0]["field"] == "bar"
    assert report.bar_diff["samples"][0]["reference"] == "absent"
    assert report.reference["export_gaps"] == 1


def test_reference_bars_outside_the_range_warn_but_pass(scene: dict[str, Any]) -> None:
    _write_export(scene["csv"], scene["bars"], scene["series"], extra_before=1, extra_after=1)
    report = _compare(scene)
    assert report.status is ParityStatus.EXACT
    assert any("predate" in warning for warning in report.warnings)
    assert any("postdate" in warning for warning in report.warnings)


def test_missing_metadata_is_unknown(scene: dict[str, Any]) -> None:
    export = import_chart_csv(scene["csv"])
    report = compare_layer1(
        export, engine_bars=scene["bars"], metadata=None, provenance=PROVENANCE
    )
    assert report.status is ParityStatus.UNKNOWN
    assert report.passed is False
    assert report.plots == {}
    assert report.run_metadata is None


def test_missing_provenance_is_unknown(scene: dict[str, Any]) -> None:
    export = import_chart_csv(scene["csv"])
    report = compare_layer1(
        export,
        engine_bars=scene["bars"],
        metadata=load_metadata(scene["meta"]),
        provenance=None,
    )
    assert report.status is ParityStatus.UNKNOWN
    assert "provenance" in (report.reason or "")


def test_fewer_than_501_bars_is_unknown(tmp_path: Path) -> None:
    bars = _bars(400)
    series = _engine(bars)
    csv_path = tmp_path / "short.csv"
    _write_export(csv_path, bars, series)
    report = compare_layer1(
        import_chart_csv(csv_path),
        engine_bars=bars,
        metadata=load_metadata(_metadata(tmp_path / "m.json")),
        provenance=PROVENANCE,
    )
    assert report.status is ParityStatus.UNKNOWN
    assert f"first {WARMUP_BARS} bars" in (report.reason or "")


def test_bar_magnifier_on_warns_but_does_not_fail(scene: dict[str, Any]) -> None:
    sidecar = _metadata(scene["meta"], bar_magnifier="ON")
    export = import_chart_csv(scene["csv"])
    report = compare_layer1(
        export,
        engine_bars=scene["bars"],
        metadata=load_metadata(sidecar),
        provenance=PROVENANCE,
    )
    assert report.status is ParityStatus.EXACT
    assert any("bar magnifier" in warning for warning in report.warnings)


# --------------------------------------------------------------------------
# run_parity gate (D-019)
# --------------------------------------------------------------------------


def test_missing_export_reports_no_reference_export(tmp_path: Path) -> None:
    run = run_parity(
        csv_path=tmp_path / "absent.csv",
        meta_path=tmp_path / "absent.meta.json",
        report_path=tmp_path / "report.json",
        engine_bars=_bars(20),
        provenance=PROVENANCE,
    )
    assert run.exit_code == EXIT_FAIL
    assert run.report is None
    assert "NO_REFERENCE_EXPORT" in run.message
    assert not (tmp_path / "report.json").exists()


def test_missing_sidecar_writes_an_unknown_report(tmp_path: Path) -> None:
    bars = _bars(BAR_COUNT)
    series = _engine(bars)
    csv_path = tmp_path / "export.csv"
    _write_export(csv_path, bars, series)
    report_path = tmp_path / "report.json"
    run = run_parity(
        csv_path=csv_path,
        meta_path=tmp_path / "absent.meta.json",
        report_path=report_path,
        engine_bars=bars,
        provenance=PROVENANCE,
    )
    assert run.exit_code == EXIT_FAIL
    assert run.report is not None and run.report.status is ParityStatus.UNKNOWN
    assert report_path.is_file()
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["status"] == "UNKNOWN"
    assert written["pass"] is False


def test_exact_run_exits_zero_and_writes_the_report(scene: dict[str, Any]) -> None:
    run = run_parity(
        csv_path=scene["csv"],
        meta_path=scene["meta"],
        report_path=scene["report"],
        engine_bars=scene["bars"],
        provenance=PROVENANCE,
    )
    assert run.exit_code == EXIT_PASS
    assert run.report is not None and run.report.status is ParityStatus.EXACT
    written = json.loads(scene["report"].read_text(encoding="utf-8"))
    assert written["status"] == "EXACT"
    assert written["engine"]["dataset_version"] == PROVENANCE["dataset_version"]
    assert set(written["plots"]) == set(PLOT_SPECS)


def test_documented_difference_passes_the_gate(scene: dict[str, Any]) -> None:
    index = 600
    value = scene["series"]["ema_regime"][index]
    assert value is not None
    _write_export(scene["csv"], scene["bars"], scene["series"], ema_override={index: f"{value:.4g}"})
    run = run_parity(
        csv_path=scene["csv"],
        meta_path=scene["meta"],
        report_path=scene["report"],
        engine_bars=scene["bars"],
        provenance=PROVENANCE,
    )
    assert run.exit_code == EXIT_PASS


def test_data_mismatch_never_exits_green(scene: dict[str, Any]) -> None:
    _write_export(scene["csv"], scene["bars"], scene["series"], close_scale={610: 1.01})
    run = run_parity(
        csv_path=scene["csv"],
        meta_path=scene["meta"],
        report_path=scene["report"],
        engine_bars=scene["bars"],
        provenance=PROVENANCE,
    )
    assert run.exit_code == EXIT_FAIL
    assert run.report is not None
    assert run.report.status is ParityStatus.DATA_MISMATCH
    assert run.report.passed is True  # spec 592 vocabulary, gate still red (D-019)


def test_no_provenance_never_exits_green(scene: dict[str, Any]) -> None:
    run = run_parity(
        csv_path=scene["csv"],
        meta_path=scene["meta"],
        report_path=scene["report"],
        engine_bars=scene["bars"],
        provenance=None,
    )
    assert run.exit_code == EXIT_FAIL
    assert run.report is not None and run.report.status is ParityStatus.UNKNOWN
