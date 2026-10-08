"""Section 14.2 layer-1 feature comparison and report assembly (T3.2).

Alignment (design note 4.3): reference and engine bars pair by timestamp;
reference bars outside the engine dataset are reported but never compared, a
missing reference bar inside the dataset is a data mismatch. OHLCV is compared
first at ``REL_TOL`` - a mismatch short-circuits to ``DATA_MISMATCH`` with a
bar diff report (first ``SAMPLE_CAP`` differences), because no feature
comparison over mismatched bars means anything. Plot values are compared only
after the protocol warm-up (skip the first ``WARMUP_BARS`` bars of both sides);
differences in the skipped region are recorded as informational observations
with the same documented-difference table but never affect the status.

Statuses follow spec line 592; ``passed`` on the report is the spec's own rule
(zero ENGINE_BUG, zero UNKNOWN) while the ``make parity`` gate additionally
requires the run to have actually compared bars (D-019). The report carries no
timestamps: same inputs -> identical document (PD-6).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from engine.features import resolve_indicator
from engine.parity.constants import REL_TOL, WARMUP_BARS
from engine.parity.importers import ReferenceExport
from engine.parity.metadata import EXPECTED_BAR_MAGNIFIER, TradingViewRunMetadata
from engine.parity.platform_differences import (
    PlatformDifference,
    find_documented_difference,
)
from engine.parity.status import ParityStatus, is_pass, overall
from exchanges.base import Kline

#: Samples kept per category in the report (design note 4.3: first 20 diffs).
SAMPLE_CAP = 20

#: PD-6 fields every parity report must embed; ``resolved_params`` and ``seed``
#: are added by :func:`compare_layer1`.
PROVENANCE_FIELDS: tuple[str, ...] = (
    "dataset_version",
    "dataset_schema_version",
    "engine_version",
    "code_version",
)


@dataclass(frozen=True, slots=True)
class PlotSpec:
    """One layer-1 plot: registry indicator, explicit params, Pine anchor."""

    indicator: str
    params: dict[str, Any]
    output: str
    anchor: str


#: Spec line 590 names the plots the export must carry; parameters come from
#: the request side of PD-11 (here: the reference study's own inputs).
PLOT_SPECS: dict[str, PlotSpec] = {
    "rvol": PlotSpec("rvol", {"length": 20}, "rvol", "rvol_climax_pro_v5.pine:77"),
    "cpos": PlotSpec("cpos", {}, "cpos", "rvol_climax_pro_v5.pine:87"),
    "upper_wick": PlotSpec("upper_wick", {}, "upper_wick", "rvol_climax_pro_v5.pine:88"),
    "ema_regime": PlotSpec("ema", {"length": 100}, "ema", "rvol_climax_pro_v5.pine:83"),
}


def engine_frame(klines: Sequence[Kline]) -> pl.DataFrame:
    """The bar frame the feature registry consumes."""
    return pl.DataFrame(
        {
            "open_time": [kline.open_time for kline in klines],
            "open": [kline.open for kline in klines],
            "high": [kline.high for kline in klines],
            "low": [kline.low for kline in klines],
            "close": [kline.close for kline in klines],
            "volume": [kline.volume for kline in klines],
        }
    )


def compute_plots(
    frame: pl.DataFrame,
) -> tuple[dict[str, list[float | None]], dict[str, str | None], dict[str, dict[str, Any]]]:
    """Resolve and compute every layer-1 plot, capturing failures per plot.

    Rule 3 (design note 4.3): an engine that cannot answer its own contract is
    an ``ENGINE_BUG``, never an ``UNKNOWN``.
    """
    series: dict[str, list[float | None]] = {}
    errors: dict[str, str | None] = {}
    resolved_params: dict[str, dict[str, Any]] = {}
    for name, plot in PLOT_SPECS.items():
        try:
            spec, resolved = resolve_indicator(plot.indicator, plot.params)
            values = spec.compute(frame, resolved)
        except Exception as exc:  # any engine failure is ENGINE_BUG (note 4.3 rule 3)
            errors[name] = f"{type(exc).__name__}: {exc}"
            series[name] = [None] * frame.height
            resolved_params[name] = dict(plot.params)
            continue
        series[name] = list(values[plot.output])
        resolved_params[name] = dict(resolved)
        errors[name] = None
    return series, errors, resolved_params


def _relative_difference(reference: float, engine: float) -> float:
    if reference == engine:
        return 0.0
    scale = max(abs(reference), abs(engine))
    return abs(reference - engine) / scale if scale else 0.0


def _classify_value(
    reference: float | None,
    engine: float | None,
    *,
    bar_index: int,
    raw: str,
) -> tuple[bool, float | None, PlatformDifference | None]:
    """(equal within REL_TOL, relative difference, documented entry if differing)."""
    if reference is None or engine is None:
        if reference is None and engine is None:
            return True, None, None
        relative: float | None = None
    else:
        relative = _relative_difference(reference, engine)
        if relative <= REL_TOL:
            return True, relative, None
    return False, relative, find_documented_difference(
        reference, engine, bar_index=bar_index, raw=raw
    )


@dataclass(frozen=True, slots=True)
class PlotResult:
    """One plot's classification over the comparison window."""

    name: str
    status: ParityStatus
    compared: int
    matched: int
    documented: int
    unexplained: int
    max_rel_diff: float | None
    detail: str | None
    samples: tuple[dict[str, Any], ...]
    anchor: str

    def to_document(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "compared": self.compared,
            "matched": self.matched,
            "documented": self.documented,
            "unexplained": self.unexplained,
            "max_rel_diff": self.max_rel_diff,
            "tolerance": REL_TOL,
            "detail": self.detail,
            "samples": [dict(sample) for sample in self.samples],
            "anchor": self.anchor,
        }


@dataclass(frozen=True, slots=True)
class ParityReport:
    """The layer-1 run record written to disk (PD-6 provenance embedded)."""

    status: ParityStatus
    passed: bool
    reason: str | None
    plots: dict[str, PlotResult]
    reference: dict[str, Any]
    run_metadata: dict[str, Any] | None
    provenance: dict[str, Any]
    warmup: dict[str, Any] | None
    bar_diff: dict[str, Any] | None
    warnings: list[str]

    def to_document(self) -> dict[str, Any]:
        return {
            "kind": "parity_layer1",
            "status": self.status.value,
            "pass": self.passed,
            "reason": self.reason,
            "plots": {name: result.to_document() for name, result in self.plots.items()},
            "reference": dict(self.reference),
            "run_metadata": dict(self.run_metadata) if self.run_metadata else None,
            "engine": dict(self.provenance),
            "warmup": dict(self.warmup) if self.warmup else None,
            "bar_diff": dict(self.bar_diff) if self.bar_diff else None,
            "warnings": list(self.warnings),
        }

    def write(self, path: Path) -> None:
        """Persist the report deterministically (sorted keys, one trailing NL)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        document = json.dumps(self.to_document(), indent=2, sort_keys=True) + "\n"
        path.write_text(document, encoding="utf-8")


def _metadata_document(metadata: TradingViewRunMetadata | None) -> dict[str, Any] | None:
    return metadata.to_document() if metadata is not None else None


def _unknown_report(
    reason: str,
    *,
    reference: dict[str, Any],
    metadata: TradingViewRunMetadata | None,
    provenance: Mapping[str, Any] | None,
    warnings: list[str],
) -> ParityReport:
    """Fail closed: an uncomparable run is UNKNOWN, never a pass (4.2)."""
    return ParityReport(
        status=ParityStatus.UNKNOWN,
        passed=False,
        reason=reason,
        plots={},
        reference=dict(reference),
        run_metadata=_metadata_document(metadata),
        provenance=dict(provenance) if provenance is not None else {},
        warmup=None,
        bar_diff=None,
        warnings=warnings,
    )


def _mismatch_report(
    bar_diffs: list[dict[str, Any]],
    *,
    reference: dict[str, Any],
    metadata: TradingViewRunMetadata | None,
    provenance: Mapping[str, Any],
    warnings: list[str],
) -> ParityReport:
    """Spec line 590: OHLCV differs -> DATA_MISMATCH plus a bar diff report."""
    plots = {
        name: PlotResult(
            name=name,
            status=ParityStatus.DATA_MISMATCH,
            compared=0,
            matched=0,
            documented=0,
            unexplained=0,
            max_rel_diff=None,
            detail="not compared: bar-level data mismatch (spec line 590)",
            samples=(),
            anchor=PLOT_SPECS[name].anchor,
        )
        for name in PLOT_SPECS
    }
    return ParityReport(
        status=ParityStatus.DATA_MISMATCH,
        passed=is_pass(ParityStatus.DATA_MISMATCH),
        reason="reference OHLCV does not match the engine dataset within tolerance",
        plots=plots,
        reference=dict(reference),
        run_metadata=_metadata_document(metadata),
        provenance=dict(provenance),
        warmup=None,
        bar_diff={
            "count": len(bar_diffs),
            "samples": bar_diffs[:SAMPLE_CAP],
            "report": f"first {min(len(bar_diffs), SAMPLE_CAP)} of {len(bar_diffs)} differences",
        },
        warnings=warnings,
    )


def compare_layer1(
    reference: ReferenceExport,
    *,
    engine_bars: Sequence[Kline],
    metadata: TradingViewRunMetadata | None,
    provenance: Mapping[str, Any] | None,
) -> ParityReport:
    """Run the section 14.2 layer-1 comparison over one import and one dataset."""
    warnings: list[str] = []
    reference_info: dict[str, Any] = {"source": reference.source, "bars": len(reference.bars)}

    if provenance is None:
        return _unknown_report(
            "engine provenance is incomplete (PD-6): " + ", ".join(PROVENANCE_FIELDS),
            reference=reference_info,
            metadata=metadata,
            provenance=None,
            warnings=warnings,
        )
    missing = [
        field for field in PROVENANCE_FIELDS if provenance.get(field) in (None, "")
    ]
    if missing:
        return _unknown_report(
            "engine provenance is incomplete (PD-6): " + ", ".join(missing),
            reference=reference_info,
            metadata=metadata,
            provenance=provenance,
            warnings=warnings,
        )
    if metadata is None:
        return _unknown_report(
            "tradingview run metadata is missing or unreadable (spec line 593)",
            reference=reference_info,
            metadata=None,
            provenance=provenance,
            warnings=warnings,
        )
    if metadata.bar_magnifier.upper() != EXPECTED_BAR_MAGNIFIER:
        warnings.append(
            f"bar magnifier is {metadata.bar_magnifier!r}, expected {EXPECTED_BAR_MAGNIFIER!r} "
            "(spec line 593 - check the platform-difference list)"
        )
    if not engine_bars:
        return _unknown_report(
            "the engine dataset holds no bars",
            reference=reference_info,
            metadata=metadata,
            provenance=provenance,
            warnings=warnings,
        )

    engine_index = {kline.open_time: index for index, kline in enumerate(engine_bars)}
    engine_start = engine_bars[0].open_time
    engine_end = engine_bars[-1].open_time
    ref_start = reference.bars[0].time_ms
    ref_end = reference.bars[-1].time_ms
    matched: list[tuple[int, int]] = []
    matched_times: set[int] = set()
    before = 0
    after = 0
    missing_inside: list[int] = []
    for ref_index, bar in enumerate(reference.bars):
        engine_i = engine_index.get(bar.time_ms)
        if engine_i is not None:
            matched.append((ref_index, engine_i))
            matched_times.add(bar.time_ms)
        elif bar.time_ms < engine_start:
            before += 1
        elif bar.time_ms > engine_end:
            after += 1
        else:
            missing_inside.append(ref_index)
    # The export must also cover every engine bar inside its own time range: an
    # engine bar the export skips (but claims to span) is a data mismatch, while
    # an engine bar the export never claimed is reported and not compared.
    export_gaps: list[int] = []
    outside_export = 0
    for kline in engine_bars:
        if kline.open_time in matched_times:
            continue
        if ref_start <= kline.open_time <= ref_end:
            export_gaps.append(kline.open_time)
        else:
            outside_export += 1
    reference_info.update(
        {
            "matched": len(matched),
            "reference_only_before": before,
            "reference_only_after": after,
            "missing_inside": len(missing_inside),
            "export_gaps": len(export_gaps),
            "engine_only": outside_export,
        }
    )
    if before:
        warnings.append(f"{before} reference bars predate the engine dataset (reported, not compared)")
    if after:
        warnings.append(f"{after} reference bars postdate the engine dataset (reported, not compared)")
    if outside_export:
        warnings.append(
            f"{outside_export} engine bars lie outside the export range "
            "(reported, not compared)"
        )

    # Bar-level data first (design note 4.2): no feature comparison over
    # mismatched bars means anything.
    relative: float | None  # declared here: reused by the plot loops below
    bar_diffs: list[dict[str, Any]] = []
    for ref_index, engine_i in matched:
        ref_bar = reference.bars[ref_index]
        engine_bar = engine_bars[engine_i]
        fields = (
            ("open", ref_bar.open, engine_bar.open),
            ("high", ref_bar.high, engine_bar.high),
            ("low", ref_bar.low, engine_bar.low),
            ("close", ref_bar.close, engine_bar.close),
            ("volume", ref_bar.volume, engine_bar.volume),
        )
        for field_name, reference_value, engine_value in fields:
            relative = _relative_difference(reference_value, engine_value)
            if relative > REL_TOL:
                bar_diffs.append(
                    {
                        "time_ms": ref_bar.time_ms,
                        "field": field_name,
                        "reference": reference_value,
                        "engine": engine_value,
                        "rel_diff": relative,
                    }
                )
    for ref_index in missing_inside:
        bar_diffs.append(
            {
                "time_ms": reference.bars[ref_index].time_ms,
                "field": "bar",
                "reference": "present",
                "engine": "absent",
                "rel_diff": None,
            }
        )
    for open_time in export_gaps:
        bar_diffs.append(
            {
                "time_ms": open_time,
                "field": "bar",
                "reference": "absent",
                "engine": "present",
                "rel_diff": None,
            }
        )
    if bar_diffs:
        return _mismatch_report(
            bar_diffs,
            reference=reference_info,
            metadata=metadata,
            provenance=provenance,
            warnings=warnings,
        )

    window = [
        (ref_index, engine_i)
        for ref_index, engine_i in matched
        if ref_index >= WARMUP_BARS and engine_i >= WARMUP_BARS
    ]
    warmup_rows = [
        (ref_index, engine_i)
        for ref_index, engine_i in matched
        if not (ref_index >= WARMUP_BARS and engine_i >= WARMUP_BARS)
    ]
    reference_info["compared_rows"] = len(window)
    if not window:
        return _unknown_report(
            f"no bars inside the comparison window (skip the first {WARMUP_BARS} bars)",
            reference=reference_info,
            metadata=metadata,
            provenance=provenance,
            warnings=warnings,
        )

    series, errors, resolved_params = compute_plots(engine_frame(engine_bars))
    provenance_doc = dict(provenance)
    provenance_doc["resolved_params"] = resolved_params
    provenance_doc["seed"] = provenance.get("seed")

    warmup_documented: list[dict[str, Any]] = []
    warmup_unexplained: list[dict[str, Any]] = []
    warmup_documented_count = 0
    warmup_unexplained_count = 0
    for ref_index, engine_i in warmup_rows:
        ref_bar = reference.bars[ref_index]
        for name in PLOT_SPECS:
            if errors[name] is not None:
                continue
            equal, relative, entry = _classify_value(
                ref_bar.plots[name],
                series[name][engine_i],
                bar_index=ref_index,
                raw=ref_bar.plot_raw[name],
            )
            if equal:
                continue
            if entry is None:
                warmup_unexplained_count += 1
                if len(warmup_unexplained) < SAMPLE_CAP:
                    warmup_unexplained.append(
                        {
                            "time_ms": ref_bar.time_ms,
                            "plot": name,
                            "reference": ref_bar.plots[name],
                            "engine": series[name][engine_i],
                            "rel_diff": relative,
                        }
                    )
            else:
                warmup_documented_count += 1
                if len(warmup_documented) < SAMPLE_CAP:
                    warmup_documented.append(
                        {
                            "time_ms": ref_bar.time_ms,
                            "plot": name,
                            "reference": ref_bar.plots[name],
                            "engine": series[name][engine_i],
                            "rel_diff": relative,
                            "entry": entry.name,
                        }
                    )

    plots: dict[str, PlotResult] = {}
    for name, plot_spec in PLOT_SPECS.items():
        if errors[name] is not None:
            plots[name] = PlotResult(
                name=name,
                status=ParityStatus.ENGINE_BUG,
                compared=len(window),
                matched=0,
                documented=0,
                unexplained=0,
                max_rel_diff=None,
                detail=f"engine failed to compute the plot: {errors[name]}",
                samples=(),
                anchor=plot_spec.anchor,
            )
            continue
        compared = 0
        matched_count = 0
        documented_count = 0
        unexplained_count = 0
        max_rel: float | None = None
        samples: list[dict[str, Any]] = []
        for ref_index, engine_i in window:
            ref_bar = reference.bars[ref_index]
            ref_value = ref_bar.plots[name]
            eng_value = series[name][engine_i]
            compared += 1
            equal, relative, entry = _classify_value(
                ref_value,
                eng_value,
                bar_index=ref_index,
                raw=ref_bar.plot_raw[name],
            )
            if equal:
                matched_count += 1
            elif entry is not None:
                documented_count += 1
            else:
                unexplained_count += 1
                if len(samples) < SAMPLE_CAP:
                    samples.append(
                        {
                            "time_ms": ref_bar.time_ms,
                            "reference": ref_value,
                            "engine": eng_value,
                            "rel_diff": relative,
                        }
                    )
            if relative is not None:
                max_rel = relative if max_rel is None else max(max_rel, relative)
        if unexplained_count:
            status = ParityStatus.ENGINE_BUG
            detail = f"{unexplained_count} values differ with no documented explanation"
        elif documented_count:
            status = ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE
            detail = f"{documented_count} values differ within a documented platform difference"
        else:
            status = ParityStatus.EXACT
            detail = None
        plots[name] = PlotResult(
            name=name,
            status=status,
            compared=compared,
            matched=matched_count,
            documented=documented_count,
            unexplained=unexplained_count,
            max_rel_diff=max_rel,
            detail=detail,
            samples=tuple(samples),
            anchor=plot_spec.anchor,
        )

    status = overall({name: result.status for name, result in plots.items()})
    warmup = {
        "rows": len(warmup_rows),
        "note": (
            f"excluded from status by the spec's skip-the-first-{WARMUP_BARS}-bars rule "
            "(line 590); informational only"
        ),
        "documented_differences": warmup_documented_count,
        "unexplained_differences": warmup_unexplained_count,
        "documented_samples": warmup_documented,
        "unexplained_samples": warmup_unexplained,
    }
    return ParityReport(
        status=status,
        passed=is_pass(status),
        reason=None,
        plots=plots,
        reference=reference_info,
        run_metadata=metadata.to_document(),
        provenance=provenance_doc,
        warmup=warmup,
        bar_diff=None,
        warnings=warnings,
    )


def unknown_report(
    reason: str,
    *,
    reference: dict[str, Any],
    metadata: TradingViewRunMetadata | None,
    provenance: Mapping[str, Any] | None,
    warnings: list[str] | None = None,
) -> ParityReport:
    """Public fail-closed factory (used by the CLI for import/metadata failures)."""
    return _unknown_report(
        reason,
        reference=reference,
        metadata=metadata,
        provenance=provenance,
        warnings=list(warnings) if warnings else [],
    )
