"""M3 (T3.2) - section 14.2 layer-1 parity tool.

Acceptance evidence produced here:
- the CSV importer accepts the documented header aliases, epoch/ISO/dot times
  and the ``na`` plot tokens, and rejects every malformed export (unknown or
  missing column, unparsable time, null OHLCV, ragged row, non-increasing
  time, empty file) instead of coercing (PD-10, design note 4.2);
- run metadata with a missing field or sidecar fails closed (PD-11);
- the classification order of design note 4.3: OHLCV mismatch with no
  documented explanation -> DATA_MISMATCH with a bar diff report; a volume
  difference inside the documented feed bound -> recorded as a documented
  platform difference and the comparison continues (4.6); engine failure ->
  ENGINE_BUG; unexplained plot differences -> ENGINE_BUG; rounded export
  values, the TV volume feed (volume and its contaminated rvol window) and
  the regime-EMA seed wash-out -> EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE;
  < 501 comparable bars -> UNKNOWN (spec line 590's skip-the-first-500 rule);
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
    worst_of,
)
from engine.parity.compare import PLOT_SPECS, PlotSpec, compute_plots
from engine.parity.importers import NA_TOKENS, REQUIRED_PLOTS
from engine.parity.metadata import EXPECTED_BAR_MAGNIFIER, REQUIRED_FIELDS
from engine.parity.platform_differences import (
    PLATFORM_DIFFERENCES,
    DifferenceContext,
    find_documented_difference,
)
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
    volume_scale: dict[int, float] | None = None,
    plot_scale: dict[str, dict[int, float]] | None = None,
) -> None:
    """Write a chart-data export whose plot columns come from the engine."""
    rows: list[list[str]] = []
    for _ in range(extra_before):
        first = bars[0]
        rows.append(
            [
                str(first.open_time - 3 * DAY),
                "100",
                "101",
                "99",
                "100",
                "10",
                "1",
                "0.5",
                "0.5",
                "100",
            ]
        )
    for index, bar in enumerate(bars):
        if skip and index in skip:
            continue
        close = bar.close * (close_scale or {}).get(index, 1.0)
        volume = bar.volume * (volume_scale or {}).get(index, 1.0)
        cells = [str(bar.open_time)]
        cells += [f"{value:.12g}" for value in (bar.open, bar.high, bar.low, close, volume)]
        for name in REQUIRED_PLOTS:
            if name == "ema_regime" and ema_override and index in ema_override:
                cells.append(ema_override[index])
                continue
            value = series[name][index]
            if value is not None and plot_scale and name in plot_scale:
                value *= plot_scale[name].get(index, 1.0)
            cells.append("#" if value is None else f"{value:.12g}")
        rows.append(cells)
    for _ in range(extra_after):
        last = bars[-1]
        rows.append(
            [
                str(last.open_time + 3 * DAY),
                "100",
                "101",
                "99",
                "100",
                "10",
                "1",
                "0.5",
                "0.5",
                "100",
            ]
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
    assert (
        overall(
            {
                "a": ParityStatus.DATA_MISMATCH,
                "b": ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE,
            }
        )
        is ParityStatus.DATA_MISMATCH
    )
    assert overall({"a": ParityStatus.EXACT, "b": ParityStatus.EXACT}) is ParityStatus.EXACT


def test_worst_of_combines_any_status_set() -> None:
    assert worst_of() is ParityStatus.UNKNOWN
    assert worst_of(ParityStatus.EXACT) is ParityStatus.EXACT
    assert (
        worst_of(ParityStatus.EXACT, ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE)
        is ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE
    )
    assert (
        worst_of(ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE, ParityStatus.DATA_MISMATCH)
        is ParityStatus.DATA_MISMATCH
    )
    assert worst_of(ParityStatus.DATA_MISMATCH, ParityStatus.ENGINE_BUG) is (
        ParityStatus.ENGINE_BUG
    )
    assert worst_of(ParityStatus.UNKNOWN, ParityStatus.EXACT) is ParityStatus.UNKNOWN


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
    rounded = find_documented_difference(400.1, engine, bar_index=600, raw="400.1")
    assert rounded is not None and rounded.name == "tv_export_rounding"
    assert find_documented_difference(399.0, engine, bar_index=600, raw="399.0") is None


def test_na_entry_only_inside_the_warmup() -> None:
    inside = find_documented_difference(None, 1.0, bar_index=WARMUP_BARS - 1, raw="#")
    assert inside is not None and inside.name == "tv_na_vs_engine_null"
    assert find_documented_difference(None, 1.0, bar_index=WARMUP_BARS, raw="#") is None
    assert find_documented_difference(None, None, bar_index=0, raw="#") is None


def test_volume_feed_entry_documents_bar_volume_within_bound() -> None:
    documented = find_documented_difference(
        1000.5,
        1000.0,
        bar_index=600,
        raw="",
        context=DifferenceContext(field="volume", engine_index=600),
    )
    assert documented is not None and documented.name == "tv_volume_feed"
    # the same field beyond the documented bound stays unexplained
    assert (
        find_documented_difference(
            1002.0,
            1000.0,
            bar_index=600,
            raw="",
            context=DifferenceContext(field="volume", engine_index=600),
        )
        is None
    )
    # a non-volume OHLCV field is never documented by this entry
    assert (
        find_documented_difference(
            1000.5,
            1000.0,
            bar_index=600,
            raw="",
            context=DifferenceContext(field="close", engine_index=600),
        )
        is None
    )


def test_volume_feed_entry_covers_contaminated_rvol_only() -> None:
    inside = find_documented_difference(
        1.0005,
        1.0,
        bar_index=600,
        raw="",
        context=DifferenceContext(
            field="rvol", engine_index=570, rvol_contaminated=frozenset({570})
        ),
    )
    assert inside is not None and inside.name == "tv_volume_feed"
    outside = find_documented_difference(
        1.0005,
        1.0,
        bar_index=600,
        raw="",
        context=DifferenceContext(
            field="rvol", engine_index=590, rvol_contaminated=frozenset({570})
        ),
    )
    assert outside is None


def test_ema_seed_entry_only_near_window_start_and_within_bound() -> None:
    near = find_documented_difference(
        91584.5,
        91584.0,
        bar_index=600,
        raw="",
        context=DifferenceContext(field="ema_regime", engine_index=505),
    )
    assert near is not None and near.name == "ema_seed_history"
    too_late = find_documented_difference(
        91584.5,
        91584.0,
        bar_index=800,
        raw="",
        context=DifferenceContext(field="ema_regime", engine_index=WARMUP_BARS + 100),
    )
    assert too_late is None
    too_big = find_documented_difference(
        91586.0,
        91584.0,
        bar_index=600,
        raw="",
        context=DifferenceContext(field="ema_regime", engine_index=505),
    )
    assert too_big is None
    # the entry explains this plot only
    assert (
        find_documented_difference(
            1.000004,
            1.0,
            bar_index=600,
            raw="",
            context=DifferenceContext(field="cpos", engine_index=505),
        )
        is None
    )


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


def test_engine_failure_is_engine_bug(
    scene: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(PLOT_SPECS, "rvol", PlotSpec("bogus_indicator", {}, "bogus", "test-anchor"))
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


def test_documented_volume_bar_keeps_the_comparison_running(scene: dict[str, Any]) -> None:
    _write_export(scene["csv"], scene["bars"], scene["series"], volume_scale={560: 1.0005})
    report = _compare(scene)
    assert report.status is ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE
    assert report.passed is True
    assert report.reason is None
    assert report.bar_diff is not None
    assert report.bar_diff["count"] == 1
    assert report.bar_diff["documented"] == 1
    assert report.bar_diff["unexplained"] == 0
    assert report.bar_diff["samples"][0]["entry"] == "tv_volume_feed"
    for plot in report.plots.values():
        assert plot.status is ParityStatus.EXACT
        assert plot.compared == BAR_COUNT - WARMUP_BARS


def test_volume_difference_beyond_the_documented_bound_is_mismatch(
    scene: dict[str, Any],
) -> None:
    _write_export(scene["csv"], scene["bars"], scene["series"], volume_scale={560: 1.002})
    report = _compare(scene)
    assert report.status is ParityStatus.DATA_MISMATCH
    assert report.bar_diff is not None
    assert report.bar_diff["documented"] == 0
    assert report.bar_diff["unexplained"] == 1
    assert "entry" not in report.bar_diff["samples"][0]


def test_close_difference_within_the_volume_bound_is_never_documented(
    scene: dict[str, Any],
) -> None:
    _write_export(scene["csv"], scene["bars"], scene["series"], close_scale={560: 1.0005})
    report = _compare(scene)
    assert report.status is ParityStatus.DATA_MISMATCH
    assert report.bar_diff is not None
    assert report.bar_diff["samples"][0]["field"] == "close"
    assert report.bar_diff["unexplained"] == 1


def test_rvol_inside_the_contaminated_window_is_documented(scene: dict[str, Any]) -> None:
    contaminated = dict.fromkeys(range(560, 581), 1.0005)  # volume bar 560 + its 20-bar mean
    _write_export(
        scene["csv"],
        scene["bars"],
        scene["series"],
        volume_scale={560: 1.0005},
        plot_scale={"rvol": contaminated},
    )
    report = _compare(scene)
    assert report.status is ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE
    rvol = report.plots["rvol"]
    assert rvol.status is ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE
    assert rvol.documented == 21
    assert rvol.unexplained == 0
    assert report.plots["cpos"].status is ParityStatus.EXACT
    assert report.plots["upper_wick"].status is ParityStatus.EXACT
    assert report.plots["ema_regime"].status is ParityStatus.EXACT


def test_rvol_outside_the_contaminated_window_is_engine_bug(scene: dict[str, Any]) -> None:
    _write_export(
        scene["csv"],
        scene["bars"],
        scene["series"],
        plot_scale={"rvol": {590: 1.0005}},
    )
    report = _compare(scene)
    assert report.status is ParityStatus.ENGINE_BUG
    assert report.passed is False
    rvol = report.plots["rvol"]
    assert rvol.unexplained == 1
    assert rvol.samples and rvol.samples[0]["rel_diff"] > REL_TOL


def test_ema_seed_washout_is_documented_only_in_the_window_head(scene: dict[str, Any]) -> None:
    scale = dict.fromkeys((500, 555, 599), 1.000004)
    override = {
        index: f"{scene['series']['ema_regime'][index] * factor:.12g}"
        for index, factor in scale.items()
    }
    _write_export(scene["csv"], scene["bars"], scene["series"], ema_override=override)
    report = _compare(scene)
    assert report.status is ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE
    ema = report.plots["ema_regime"]
    assert ema.documented == 3
    assert ema.unexplained == 0


def test_ema_difference_after_the_washout_window_is_engine_bug(scene: dict[str, Any]) -> None:
    index = 600
    value = scene["series"]["ema_regime"][index]
    assert value is not None
    _write_export(
        scene["csv"],
        scene["bars"],
        scene["series"],
        ema_override={index: f"{value * 1.000004:.12g}"},
    )
    report = _compare(scene)
    assert report.status is ParityStatus.ENGINE_BUG
    assert report.plots["ema_regime"].unexplained == 1


def test_ema_difference_beyond_the_washout_bound_is_engine_bug(scene: dict[str, Any]) -> None:
    index = 510
    value = scene["series"]["ema_regime"][index]
    assert value is not None
    _write_export(
        scene["csv"],
        scene["bars"],
        scene["series"],
        ema_override={index: f"{value * 1.00002:.12g}"},
    )
    report = _compare(scene)
    assert report.status is ParityStatus.ENGINE_BUG
    assert report.plots["ema_regime"].unexplained == 1


def test_worst_of_bar_and_plot_statuses_decides_the_run(scene: dict[str, Any]) -> None:
    value = scene["series"]["ema_regime"][610]
    assert value is not None
    _write_export(
        scene["csv"],
        scene["bars"],
        scene["series"],
        volume_scale={560: 1.0005},
        ema_override={610: f"{value * 1.01:.12g}"},
    )
    report = _compare(scene)
    assert report.status is ParityStatus.ENGINE_BUG  # worst wins over the documented bar
    assert report.bar_diff is not None
    assert report.bar_diff["documented"] == 1


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
    report = compare_layer1(export, engine_bars=scene["bars"], metadata=None, provenance=PROVENANCE)
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
    _write_export(
        scene["csv"], scene["bars"], scene["series"], ema_override={index: f"{value:.4g}"}
    )
    run = run_parity(
        csv_path=scene["csv"],
        meta_path=scene["meta"],
        report_path=scene["report"],
        engine_bars=scene["bars"],
        provenance=PROVENANCE,
    )
    assert run.exit_code == EXIT_PASS


def test_documented_volume_difference_passes_the_gate(scene: dict[str, Any]) -> None:
    _write_export(scene["csv"], scene["bars"], scene["series"], volume_scale={560: 1.0005})
    run = run_parity(
        csv_path=scene["csv"],
        meta_path=scene["meta"],
        report_path=scene["report"],
        engine_bars=scene["bars"],
        provenance=PROVENANCE,
    )
    assert run.exit_code == EXIT_PASS
    assert run.report is not None
    assert run.report.status is ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE
    written = json.loads(scene["report"].read_text(encoding="utf-8"))
    assert written["bar_diff"]["documented"] == 1
    assert written["bar_diff"]["unexplained"] == 0


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
