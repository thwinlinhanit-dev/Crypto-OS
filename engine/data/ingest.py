"""Spec section 4.5/4.6 + design note section 5: the M1 ingest pipeline.

Order of operations (design note section 5, steps 1-10): scope validation ->
storage budget gate -> per (instrument, timeframe, year) fetch-or-resume chunks
-> Guardian assessment of the raw assembly -> content-addressed writes with a
running budget -> manifest -> assessment/exclusions -> derived weekly bars ->
audit.  Every decision the spec leaves open is marked INTERPRETATION here and
carried in the design note / DECISIONS:

- a chunk whose expected closed bars are already covered by a stored part is
  reused without contacting the venue (resume, section 4.5 "existing part");
- when covered, the stored part is taken as-is; when not, the fresh fetch is
  the whole truth for that chunk (the older part is orphaned, never edited);
- repair assembles stored rows plus the gap fetch and assesses them sorted by
  ``open_time``: arrival order is meaningless across two sources, while OHLC
  and duplicate defects are still detected on values (design note section 6);
- ``dataset_version`` does not hash scope, so an ingest whose bytes equal an
  earlier repair's bytes legitimately reproduces that repair's identity (AC1).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl
from sqlalchemy import Connection, text

from engine.data.bars import (
    Timeframe,
    bar_frame,
    expected_open_times,
    is_closed,
    is_derived,
    parse_timeframe,
)
from engine.data.guardian import (
    Assessment,
    ExclusionInterval,
    ExclusionKind,
    GapRow,
    assess,
    get_gap,
    load_exclusions,
    save_assessment,
    save_exclusions,
    set_gap_status,
)
from engine.data.instruments import Instrument, MarketType, get_instrument
from engine.data.manifest import (
    DatasetManifest,
    SourceFile,
    build_manifest,
    save_manifest,
)
from engine.data.parquet import (
    Layer,
    PartRef,
    canonical_bytes,
    content_sha256,
    list_parts,
    part_exists,
    part_rel_path,
    read_part,
    write_part,
)
from engine.data.resample import WEEKLY_EXTRA_COLUMNS, resample_weekly
from engine.errors import (
    DataUnavailableError,
    NotFoundError,
    NotImplementedMarketError,
    ValidationError,
)
from exchanges.base import ExchangeAdapter, Kline

GB = 1024**3


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _dt(value_ms: int) -> datetime:
    return datetime.fromtimestamp(value_ms / 1000, tz=UTC)


def _year_of(open_ms: int) -> int:
    return datetime.fromtimestamp(open_ms / 1000, tz=UTC).year


def _year_window(year: int) -> tuple[int, int]:
    start = _ms(datetime(year, 1, 1, tzinfo=UTC))
    end = _ms(datetime(year + 1, 1, 1, tzinfo=UTC)) - 1
    return start, end


def _dir_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _budget_error(used: int, budget: int) -> ValidationError:
    """OD-5: refuse before writing, naming the measured and allowed gigabytes."""
    return ValidationError(
        "storage budget exceeded; refusing to write any bytes (OD-5)",
        {
            "reason": "storage_budget_exceeded",
            "used_gb": round(used / GB, 6),
            "budget_gb": round(budget / GB, 6),
        },
    )


def ensure_storage_budget(data_dir: Path, budget_bytes: int) -> None:
    """Public OD-5 gate: measure the storage root, refuse when at/over budget.

    The API calls this before queueing a job so an over-budget request fails
    fast with 422 ``reason:"storage_budget_exceeded"`` instead of surfacing
    later inside the background task; ``run_ingest`` / ``run_repair`` /
    ``run_resample`` call it again at entry as the enforcement of record.
    """
    running = _dir_bytes(data_dir)
    if running >= budget_bytes:
        raise _budget_error(running, budget_bytes)


def _audit(
    conn: Connection, *, actor: str, action: str, target: str, detail: dict[str, Any]
) -> None:
    conn.execute(
        text(
            "INSERT INTO system.audit_log (actor, action, target, detail) "
            "VALUES (:actor, :action, :target, CAST(:detail AS jsonb))"
        ),
        {"actor": actor, "action": action, "target": target, "detail": json.dumps(detail)},
    )


@dataclass(frozen=True, slots=True)
class IngestScope:
    """PD-12: the explicit universe every dataset result must record."""

    venue: str
    market_type: MarketType
    instrument_ids: tuple[UUID, ...]
    timeframes: tuple[Timeframe, ...]
    start_ms: int
    end_ms: int
    storage_budget_bytes: int


@dataclass(frozen=True, slots=True)
class IngestResult:
    dataset_version: str
    manifest: DatasetManifest
    files_written: int
    files_reused: int
    derived_written: int


@dataclass(frozen=True, slots=True)
class RepairResult:
    status: str  # "REPAIRED" | "UNREPAIRABLE" (section 4.6 vocabulary)
    dataset_version: str | None
    gap_id: UUID


@dataclass(slots=True)
class _Derived:
    refs: list[PartRef]
    written: int
    reused: int
    running: int
    incomplete_weeks: int
    total_weeks: int


def _resolve_scope(conn: Connection, scope: IngestScope, *, derived_ok: bool) -> list[Instrument]:
    """Fail closed on anything incoherent before the first network call (PD-11/12)."""
    if not scope.instrument_ids:
        raise ValidationError("scope names no instruments", {"instrument_ids": []})
    if not scope.timeframes:
        raise ValidationError("scope names no timeframes", {"timeframes": []})
    if scope.end_ms < scope.start_ms:
        raise ValidationError(
            "scope end is before scope start",
            {"start_ms": scope.start_ms, "end_ms": scope.end_ms},
        )
    if scope.storage_budget_bytes <= 0:
        raise ValidationError(
            "storage budget must be positive", {"storage_budget_bytes": scope.storage_budget_bytes}
        )
    if scope.market_type is MarketType.PERPETUAL:
        raise NotImplementedMarketError(
            "perpetual markets are not served before M10 (PD-9)",
            {"venue": scope.venue, "market_type": scope.market_type.value},
        )
    if not derived_ok:
        derived = [tf.value for tf in scope.timeframes if is_derived(tf)]
        if derived:
            raise ValidationError(
                "derived timeframes are produced by RESAMPLE, never by ingest",
                {"timeframes": derived},
            )
    instruments: list[Instrument] = []
    for instrument_id in sorted(scope.instrument_ids):
        instrument = get_instrument(conn, instrument_id)
        if instrument is None:
            raise NotFoundError(
                "instrument is not registered",
                {"instrument_id": str(instrument_id)},
            )
        if instrument.venue != scope.venue or instrument.market_type is not scope.market_type:
            raise ValidationError(
                "instrument does not belong to the requested (venue, market_type) scope",
                {
                    "instrument_id": str(instrument_id),
                    "venue": instrument.venue,
                    "market_type": instrument.market_type.value,
                },
            )
        instruments.append(instrument)
    return instruments


def _stamp(
    klines: Sequence[Kline],
    *,
    instrument: Instrument,
    timeframe: Timeframe,
    source: str,
    window_start_ms: int,
    window_end_ms: int,
    now_ms: int,
) -> list[dict[str, Any]]:
    """Klines -> spec rows: window-bounded and closed only (section 4.2)."""
    rows: list[dict[str, Any]] = []
    for kline in klines:
        if kline.open_time < window_start_ms or kline.open_time > window_end_ms:
            continue
        if kline.close_time > now_ms:
            continue
        rows.append(
            {
                "instrument_id": str(instrument.instrument_id),
                "timeframe": timeframe.value,
                "open_time": kline.open_time,
                "close_time": kline.close_time,
                "open": kline.open,
                "high": kline.high,
                "low": kline.low,
                "close": kline.close,
                "volume": kline.volume,
                "quote_volume": kline.quote_volume,
                "trades": kline.trades,
                "source": source,
            }
        )
    return rows


def _clean(frame: pl.DataFrame) -> pl.DataFrame:
    """Drop OHLC-invalid rows, then collapse duplicates to first arrival (4.6)."""
    if frame.is_empty():
        return frame
    valid = frame.filter(
        (pl.col("low") <= pl.col("open"))
        & (pl.col("close") <= pl.col("high"))
        & (pl.col("low") <= pl.col("high"))
    )
    return valid.sort("open_time", maintain_order=True).unique(
        subset=["open_time"], keep="first", maintain_order=True
    )


def _read_year_parts(
    data_dir: Path,
    *,
    venue: str,
    market_type: str,
    native_symbol: str,
    timeframe: Timeframe,
    year: int,
) -> list[tuple[str, pl.DataFrame]]:
    """Stored parts of one partition year (resume, design note section 5)."""
    rels = [
        rel
        for rel in list_parts(
            data_dir,
            venue=venue,
            market_type=market_type,
            native_symbol=native_symbol,
            timeframe=timeframe,
        )
        if f"/year={year}/" in rel
    ]
    return [(rel, read_part(data_dir, rel)) for rel in rels]


def _add_part(
    frame: pl.DataFrame,
    *,
    data_dir: Path,
    venue: str,
    market_type: str,
    native_symbol: str,
    timeframe: Timeframe,
    layer: Layer,
    extra_columns: tuple[str, ...],
    running: int,
    budget: int,
) -> tuple[PartRef, bool, int]:
    """Content-addressed store; the budget gate runs before any byte lands."""
    sha256 = content_sha256(frame, extra_columns)
    year = _year_of(int(frame["open_time"][0]))
    rel = part_rel_path(
        layer=layer,
        venue=venue,
        market_type=market_type,
        native_symbol=native_symbol,
        timeframe=timeframe,
        year=year,
        sha256=sha256,
    )
    if part_exists(data_dir, rel):
        return PartRef(path=rel, content_sha256=sha256, rows=frame.height), False, running
    # canonical_bytes is the conservative pre-flight estimate: it omits
    # ingested_at but is uncompressed, so it over-estimates the parquet file.
    estimate = len(canonical_bytes(frame, extra_columns))
    if running + estimate > budget:
        raise _budget_error(running, budget)
    ref = write_part(
        frame,
        data_dir=data_dir,
        venue=venue,
        market_type=market_type,
        native_symbol=native_symbol,
        timeframe=timeframe,
        layer=layer,
        extra_columns=extra_columns,
    )
    written_bytes = data_dir.joinpath(*ref.path.split("/")).stat().st_size
    return ref, True, running + written_bytes


def _store_year_parts(
    cleaned: pl.DataFrame,
    years: Sequence[int],
    *,
    data_dir: Path,
    venue: str,
    market_type: str,
    native_symbol: str,
    timeframe: Timeframe,
    layer: Layer = Layer.NORMALIZED,
    extra_columns: tuple[str, ...] = (),
    running: int,
    budget: int,
) -> tuple[list[PartRef], int, int, int]:
    """Write each partition year of ``cleaned``; returns (refs, written, reused, running)."""
    refs: list[PartRef] = []
    written = 0
    reused = 0
    for year in years:
        start, end = _year_window(year)
        chunk = cleaned.filter((pl.col("open_time") >= start) & (pl.col("open_time") <= end))
        if chunk.is_empty():
            continue
        ref, did_write, running = _add_part(
            chunk,
            data_dir=data_dir,
            venue=venue,
            market_type=market_type,
            native_symbol=native_symbol,
            timeframe=timeframe,
            layer=layer,
            extra_columns=extra_columns,
            running=running,
            budget=budget,
        )
        refs.append(ref)
        if did_write:
            written += 1
        else:
            reused += 1
    return refs, written, reused, running


def _derive_weekly(
    conn: Connection,
    instruments: Sequence[Instrument],
    *,
    venue: str,
    market_type: str,
    data_dir: Path,
    now_ms: int,
    ingested_at_ms: int,
    running: int,
    budget: int,
) -> _Derived:
    """Section 4.3: 1W from stored 1D, incomplete when a day is actively excluded."""
    derived = _Derived(
        refs=[], written=0, reused=0, running=running, incomplete_weeks=0, total_weeks=0
    )
    for instrument in instruments:
        daily_paths = list_parts(
            data_dir,
            venue=venue,
            market_type=market_type,
            native_symbol=instrument.native_symbol,
            timeframe=Timeframe.D1,
        )
        if not daily_paths:
            continue
        daily = pl.concat([read_part(data_dir, rel) for rel in daily_paths])
        intervals = [
            ExclusionInterval(
                start_ms=_ms(row.exclusion_start),
                end_ms=_ms(row.exclusion_end),
                kind=ExclusionKind(row.kind),
            )
            for row in load_exclusions(
                conn, instrument_id=instrument.instrument_id, timeframe="1D", active_only=True
            )
        ]
        weekly = resample_weekly(
            daily, now_ms=now_ms, ingested_at_ms=ingested_at_ms, exclusions=intervals
        )
        if weekly.is_empty():
            continue
        derived.incomplete_weeks += int(weekly["incomplete"].sum())
        derived.total_weeks += weekly.height
        years = sorted({_year_of(int(open_ms)) for open_ms in weekly["open_time"].to_list()})
        refs, written, reused, derived.running = _store_year_parts(
            weekly,
            years,
            data_dir=data_dir,
            venue=venue,
            market_type=market_type,
            native_symbol=instrument.native_symbol,
            timeframe=Timeframe.W1,
            layer=Layer.DERIVED,
            extra_columns=WEEKLY_EXTRA_COLUMNS,
            running=derived.running,
            budget=budget,
        )
        derived.refs.extend(refs)
        derived.written += written
        derived.reused += reused
    return derived


def _manifest(
    scope: IngestScope,
    instruments: Sequence[Instrument],
    files: Sequence[PartRef],
    assessments: Sequence[tuple[Instrument, Timeframe, Assessment]],
) -> DatasetManifest:
    ordered = sorted(files, key=lambda ref: ref.path)
    missing_ranges = [
        {
            "instrument_id": str(instrument.instrument_id),
            "timeframe": timeframe.value,
            "kind": interval.kind.value,
            "start_ms": interval.start_ms,
            "end_ms": interval.end_ms,
            "status": "OPEN",
        }
        for instrument, timeframe, assessment in assessments
        for interval in assessment.exclusions
    ]
    return build_manifest(
        venue=scope.venue,
        market_type=scope.market_type.value,
        instruments=[str(instrument.instrument_id) for instrument in instruments],
        timeframes=[timeframe.value for timeframe in sorted(scope.timeframes)],
        start_time=_dt(scope.start_ms),
        end_time=_dt(scope.end_ms),
        source_files=[
            SourceFile(path=ref.path, content_sha256=ref.content_sha256, rows=ref.rows)
            for ref in ordered
        ],
        row_counts={ref.path: ref.rows for ref in ordered},
        missing_ranges=missing_ranges,
        quality_summary={
            "series": [
                {
                    "instrument_id": str(instrument.instrument_id),
                    "timeframe": timeframe.value,
                    "state": assessment.state.value,
                    "completeness": assessment.completeness,
                    "causes": assessment.detail.get("causes", []),
                }
                for instrument, timeframe, assessment in assessments
            ]
        },
    )


def _record(
    conn: Connection,
    assessments: Sequence[tuple[Instrument, Timeframe, Assessment]],
    *,
    ingested_at_ms: int,
    source: str,
) -> None:
    detected = _dt(ingested_at_ms)
    for instrument, timeframe, assessment in assessments:
        save_assessment(conn, instrument.instrument_id, assessment, computed_at=detected)
        save_exclusions(
            conn,
            instrument_id=instrument.instrument_id,
            timeframe=timeframe.value,
            intervals=assessment.exclusions,
            source=source,
            detected_at=detected,
        )


async def run_ingest(
    conn: Connection,
    scope: IngestScope,
    adapter: ExchangeAdapter,
    *,
    data_dir: Path,
    now_ms: int,
    ingested_at_ms: int,
    actor: str = "ingest",
) -> IngestResult:
    """Design note section 5, steps 1-10 (section 4.5 pipeline)."""
    instruments = _resolve_scope(conn, scope, derived_ok=False)
    data_dir.mkdir(parents=True, exist_ok=True)
    budget = scope.storage_budget_bytes
    ensure_storage_budget(data_dir, budget)
    running = _dir_bytes(data_dir)

    years = list(range(_year_of(scope.start_ms), _year_of(scope.end_ms) + 1))
    source_files: list[PartRef] = []
    assessments: list[tuple[Instrument, Timeframe, Assessment]] = []
    files_written = 0
    files_reused = 0

    for instrument in instruments:
        for timeframe in sorted(scope.timeframes):
            raw_frames: list[pl.DataFrame] = []
            for year in years:
                chunk_start = max(scope.start_ms, _year_window(year)[0])
                chunk_end = min(scope.end_ms, _year_window(year)[1])
                requirement = [
                    open_ms
                    for open_ms in expected_open_times(chunk_start, chunk_end, timeframe)
                    if is_closed(open_ms, now_ms, timeframe)
                ]
                existing = _read_year_parts(
                    data_dir,
                    venue=scope.venue,
                    market_type=scope.market_type.value,
                    native_symbol=instrument.native_symbol,
                    timeframe=timeframe,
                    year=year,
                )
                required = set(requirement)
                covering = [
                    frame for _, frame in existing if required <= set(frame["open_time"].to_list())
                ]
                if requirement and not covering:
                    klines = await adapter.fetch_historical_klines(
                        native_symbol=instrument.native_symbol,
                        timeframe=timeframe,
                        start_ms=chunk_start,
                        end_ms=chunk_end,
                    )
                    rows = _stamp(
                        klines,
                        instrument=instrument,
                        timeframe=timeframe,
                        source=adapter.name,
                        window_start_ms=chunk_start,
                        window_end_ms=chunk_end,
                        now_ms=now_ms,
                    )
                    if rows:
                        raw_frames.append(bar_frame(rows, ingested_at_ms=ingested_at_ms))
                elif covering:
                    # Deterministic pick when several stored parts both cover:
                    # most rows first, path as the tie-break (design note 5).
                    covering.sort(key=lambda frame: (frame.height, str(frame["open_time"][0])))
                    raw_frames.append(covering[-1])
            raw = (
                pl.concat(raw_frames)
                if raw_frames
                else bar_frame([], ingested_at_ms=ingested_at_ms)
            )
            assessment = assess(
                raw,
                timeframe=timeframe,
                window_start_ms=scope.start_ms,
                window_end_ms=scope.end_ms,
                now_ms=now_ms,
                ingested_at_ms=ingested_at_ms,
            )
            assessments.append((instrument, timeframe, assessment))
            refs, written, reused, running = _store_year_parts(
                _clean(raw),
                years,
                data_dir=data_dir,
                venue=scope.venue,
                market_type=scope.market_type.value,
                native_symbol=instrument.native_symbol,
                timeframe=timeframe,
                running=running,
                budget=budget,
            )
            source_files.extend(refs)
            files_written += written
            files_reused += reused

    manifest = _manifest(scope, instruments, source_files, assessments)
    save_manifest(conn, manifest)
    _record(conn, assessments, ingested_at_ms=ingested_at_ms, source="ingest")

    derived_written = 0
    if Timeframe.D1 in scope.timeframes:
        derived = _derive_weekly(
            conn,
            instruments,
            venue=scope.venue,
            market_type=scope.market_type.value,
            data_dir=data_dir,
            now_ms=now_ms,
            ingested_at_ms=ingested_at_ms,
            running=running,
            budget=budget,
        )
        derived_written = derived.written

    _audit(
        conn,
        actor=actor,
        action="ingest",
        target=manifest.dataset_version,
        detail={
            "venue": scope.venue,
            "market_type": scope.market_type.value,
            "instrument_ids": [str(item) for item in scope.instrument_ids],
            "timeframes": [tf.value for tf in scope.timeframes],
            "start_ms": scope.start_ms,
            "end_ms": scope.end_ms,
            "files_written": files_written,
            "files_reused": files_reused,
            "derived_written": derived_written,
        },
    )
    return IngestResult(
        dataset_version=manifest.dataset_version,
        manifest=manifest,
        files_written=files_written,
        files_reused=files_reused,
        derived_written=derived_written,
    )


def _gap_ms(gap: GapRow) -> tuple[int, int]:
    return _ms(gap.exclusion_start), _ms(gap.exclusion_end)


def _span(frame: pl.DataFrame) -> tuple[int, int]:
    """Manifest window of a repaired series: first to last open (section 4.4)."""
    opens = frame["open_time"].to_list()
    return int(min(opens)), int(max(opens))


async def run_repair(
    conn: Connection,
    *,
    gap_id: UUID,
    adapter: ExchangeAdapter,
    data_dir: Path,
    now_ms: int,
    ingested_at_ms: int,
    storage_budget_bytes: int,
    actor: str = "repair",
) -> RepairResult:
    """Section 4.6 repair flow: refetch, rewrite, REPAIRED or UNREPAIRABLE."""
    gap = get_gap(conn, gap_id)
    if gap is None:
        raise NotFoundError("gap is not registered", {"gap_id": str(gap_id)})
    # INTERPRETATION (design note 6): a resolved or resolved-and-redetected
    # range is refused; OPEN and UNREPAIRABLE ranges may be retried.
    if gap.status not in {"OPEN", "UNREPAIRABLE"}:
        raise ValidationError(
            "gap is not awaiting repair",
            {"gap_id": str(gap_id), "status": gap.status},
        )
    instrument = get_instrument(conn, gap.instrument_id)
    if instrument is None:
        raise NotFoundError(
            "instrument of this gap is not registered",
            {"instrument_id": str(gap.instrument_id)},
        )
    timeframe = parse_timeframe(gap.timeframe)
    if is_derived(timeframe):
        raise ValidationError(
            "derived timeframes are repaired by RESAMPLE, not by gap repair",
            {"timeframe": timeframe.value},
        )
    data_dir.mkdir(parents=True, exist_ok=True)
    budget = storage_budget_bytes
    ensure_storage_budget(data_dir, budget)
    running = _dir_bytes(data_dir)

    gap_start, gap_end = _gap_ms(gap)
    years = list(range(_year_of(gap_start), _year_of(gap_end - 1) + 1))
    existing = [
        frame
        for year in years
        for _, frame in _read_year_parts(
            data_dir,
            venue=instrument.venue,
            market_type=instrument.market_type.value,
            native_symbol=instrument.native_symbol,
            timeframe=timeframe,
            year=year,
        )
    ]
    klines = await adapter.fetch_historical_klines(
        native_symbol=instrument.native_symbol,
        timeframe=timeframe,
        start_ms=gap_start,
        end_ms=gap_end - 1,
    )
    fetched_rows = _stamp(
        klines,
        instrument=instrument,
        timeframe=timeframe,
        source=adapter.name,
        window_start_ms=gap_start,
        window_end_ms=gap_end - 1,
        now_ms=now_ms,
    )
    fetched = bar_frame(fetched_rows, ingested_at_ms=ingested_at_ms)
    # Sorted assembly: arrival order is meaningless across stored + fetched
    # sources; OHLC and duplicate defects are still detected on values.
    pieces = [*existing, fetched] if fetched_rows else existing
    if pieces:
        raw = pl.concat(pieces).sort("open_time", maintain_order=True)
        start_ms, end_ms = _span(raw)
    else:
        raw = bar_frame([], ingested_at_ms=ingested_at_ms)
        start_ms, end_ms = gap_start, gap_end - 1
    assessment = assess(
        raw,
        timeframe=timeframe,
        window_start_ms=start_ms,
        window_end_ms=end_ms,
        now_ms=now_ms,
        ingested_at_ms=ingested_at_ms,
    )
    cleaned = _clean(raw)
    required = [
        open_ms
        for open_ms in expected_open_times(gap_start, gap_end - 1, timeframe)
        if is_closed(open_ms, now_ms, timeframe)
    ]
    present = set(cleaned["open_time"].to_list())

    result_version: str | None = None
    if required and not all(open_ms in present for open_ms in required):
        status = "UNREPAIRABLE"
        _record(
            conn,
            [(instrument, timeframe, assessment)],
            ingested_at_ms=ingested_at_ms,
            source="repair",
        )
    else:
        refs, _, _, running = _store_year_parts(
            cleaned,
            years,
            data_dir=data_dir,
            venue=instrument.venue,
            market_type=instrument.market_type.value,
            native_symbol=instrument.native_symbol,
            timeframe=timeframe,
            running=running,
            budget=budget,
        )
        ordered = sorted(refs, key=lambda ref: ref.path)
        manifest = build_manifest(
            venue=instrument.venue,
            market_type=instrument.market_type.value,
            instruments=[str(instrument.instrument_id)],
            timeframes=[timeframe.value],
            start_time=_dt(start_ms),
            end_time=_dt(end_ms),
            source_files=[
                SourceFile(path=ref.path, content_sha256=ref.content_sha256, rows=ref.rows)
                for ref in ordered
            ],
            row_counts={ref.path: ref.rows for ref in ordered},
            missing_ranges=[],
            quality_summary={
                "series": [
                    {
                        "instrument_id": str(instrument.instrument_id),
                        "timeframe": timeframe.value,
                        "state": assessment.state.value,
                        "completeness": assessment.completeness,
                        "causes": assessment.detail.get("causes", []),
                    }
                ]
            },
        )
        save_manifest(conn, manifest)
        _record(
            conn,
            [(instrument, timeframe, assessment)],
            ingested_at_ms=ingested_at_ms,
            source="repair",
        )
        result_version = manifest.dataset_version
        status = "REPAIRED"

    set_gap_status(
        conn,
        gap_id,
        status=status,
        repaired_at=_dt(ingested_at_ms) if status == "REPAIRED" else None,
        repaired_dataset_version=result_version,
    )
    _derive_weekly(
        conn,
        [instrument],
        venue=instrument.venue,
        market_type=instrument.market_type.value,
        data_dir=data_dir,
        now_ms=now_ms,
        ingested_at_ms=ingested_at_ms,
        running=running,
        budget=budget,
    )
    _audit(
        conn,
        actor=actor,
        action="repair",
        target=str(gap_id),
        detail={"status": status, "dataset_version": result_version, "timeframe": timeframe.value},
    )
    return RepairResult(status=status, dataset_version=result_version, gap_id=gap_id)


def run_resample(
    conn: Connection,
    scope: IngestScope,
    *,
    data_dir: Path,
    now_ms: int,
    ingested_at_ms: int,
    actor: str = "resample",
) -> IngestResult:
    """Section 4.3: derive 1W from stored 1D; DATA_UNAVAILABLE when none exists."""
    if scope.timeframes != (Timeframe.W1,):
        raise ValidationError(
            "resample serves only the derived 1W timeframe",
            {"timeframes": [timeframe.value for timeframe in scope.timeframes]},
        )
    instruments = _resolve_scope(conn, scope, derived_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    budget = scope.storage_budget_bytes
    ensure_storage_budget(data_dir, budget)
    running = _dir_bytes(data_dir)

    for instrument in instruments:
        daily_paths = list_parts(
            data_dir,
            venue=scope.venue,
            market_type=scope.market_type.value,
            native_symbol=instrument.native_symbol,
            timeframe=Timeframe.D1,
        )
        if not daily_paths:
            raise DataUnavailableError(
                "no stored 1D bars to resample; ingest daily data first",
                {
                    "venue": scope.venue,
                    "market_type": scope.market_type.value,
                    "native_symbol": instrument.native_symbol,
                    "timeframe": "1W",
                },
            )

    derived = _derive_weekly(
        conn,
        instruments,
        venue=scope.venue,
        market_type=scope.market_type.value,
        data_dir=data_dir,
        now_ms=now_ms,
        ingested_at_ms=ingested_at_ms,
        running=running,
        budget=budget,
    )
    ordered = sorted(derived.refs, key=lambda ref: ref.path)
    manifest = build_manifest(
        venue=scope.venue,
        market_type=scope.market_type.value,
        instruments=[str(instrument.instrument_id) for instrument in instruments],
        timeframes=[Timeframe.W1.value],
        start_time=_dt(scope.start_ms),
        end_time=_dt(scope.end_ms),
        source_files=[
            SourceFile(path=ref.path, content_sha256=ref.content_sha256, rows=ref.rows)
            for ref in ordered
        ],
        row_counts={ref.path: ref.rows for ref in ordered},
        missing_ranges=[],
        quality_summary={
            "source_timeframe": "1D",
            "weeks_total": derived.total_weeks,
            "weeks_incomplete": derived.incomplete_weeks,
        },
    )
    save_manifest(conn, manifest)
    _audit(
        conn,
        actor=actor,
        action="resample",
        target=manifest.dataset_version,
        detail={
            "venue": scope.venue,
            "market_type": scope.market_type.value,
            "instrument_ids": [str(item) for item in scope.instrument_ids],
            "derived_written": derived.written,
            "derived_reused": derived.reused,
        },
    )
    return IngestResult(
        dataset_version=manifest.dataset_version,
        manifest=manifest,
        files_written=0,
        files_reused=0,
        derived_written=derived.written,
    )
