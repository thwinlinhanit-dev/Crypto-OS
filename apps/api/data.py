"""Spec section 11 (M1 subset) + design note section 10: the data endpoints.

Reads (instruments, dataset manifests, health, gaps, jobs) serve engine tables
directly.  ``POST /v1/data/ingest`` and ``POST /v1/data/repair`` only *queue*
a ``system.job`` row and schedule ``run_job`` as an ASGI background task, so
the spec's job contract (202 ``{job_id,status:"queued"}``) holds while the
engine keeps a single dispatch path (design note section 5).

Fail-closed decisions recorded here (PD-4/PD-10/PD-11):
- ``market_type=PERPETUAL`` -> 501 ``NOT_IMPLEMENTED`` before anything is
  queued (AC4);
- a venue other than BINANCE -> 501: M1 ingests the live Binance backfill
  only, and building another venue's adapter would silently substitute data;
- an unknown timeframe or a mixed native+derived scope -> 422
  (``resolve_job_kind``);
- the storage budget is checked *before* queueing (OD-5 / AC5): resolved
  from the request or ``config/defaults.yaml`` (PD-11), refusing with
  ``reason:"storage_budget_exceeded"``;
- times are ISO-8601 UTC; ``to`` must be after ``from``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.db import connection_scope
from apps.api.jobs import run_job
from apps.api.settings import get_settings
from engine.config.defaults import load_defaults
from engine.data.bars import UnknownTimeframeError, parse_timeframe
from engine.data.guardian import (
    GapRow,
    QualityRecord,
    get_gap,
    latest_assessments,
    load_exclusions,
)
from engine.data.ingest import GB, ensure_storage_budget
from engine.data.instruments import Instrument, MarketType, get_instrument, list_instruments
from engine.data.manifest import DatasetManifest, get_manifest
from engine.errors import NotFoundError, NotImplementedMarketError, ValidationError
from engine.jobs import JobKind, JobRecord, enqueue_job, get_job, resolve_job_kind

router = APIRouter(prefix="/v1", tags=["data"])

_GAP_STATUSES = ("OPEN", "REPAIRED", "UNREPAIRABLE")


# --------------------------------------------------------------------------
# Request / response bodies
# --------------------------------------------------------------------------


class IngestRequest(BaseModel):
    """POST /v1/data/ingest body (spec section 11; times are ISO-8601 UTC)."""

    model_config = ConfigDict(populate_by_name=True)

    venue: str
    market_type: str
    instrument_ids: list[str]
    timeframes: list[str]
    from_: datetime = Field(alias="from")
    to: datetime
    storage_budget_gb: float | None = None

    @field_validator("from_", "to")
    @classmethod
    def _must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("times must be ISO-8601 UTC (offset Z)")
        return value

    @model_validator(mode="after")
    def _window(self) -> IngestRequest:
        if self.to <= self.from_:
            raise ValueError("to must be after from")
        if self.storage_budget_gb is not None and self.storage_budget_gb <= 0:
            raise ValueError("storage_budget_gb must be positive")
        return self


class RepairRequest(BaseModel):
    """POST /v1/data/repair body: the stored gap to refetch."""

    gap_id: UUID
    storage_budget_gb: float | None = None

    @field_validator("storage_budget_gb")
    @classmethod
    def _positive(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("storage_budget_gb must be positive")
        return value


class JobQueued(BaseModel):
    """Spec section 11: POST answers ``{"job_id","status":"queued"}`` (202)."""

    job_id: UUID
    status: Literal["queued"]


class JobTimestamps(BaseModel):
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobView(BaseModel):
    """GET /v1/jobs/{id} payload (spec section 11)."""

    job_id: UUID
    kind: str
    status: str
    progress: float
    result_ref: str | None
    params: dict[str, Any]
    logs: list[str]
    error: str | None
    timestamps: JobTimestamps


class InstrumentView(BaseModel):
    instrument_id: UUID
    venue: str
    market_type: str
    asset: str
    quote: str
    native_symbol: str
    tick_size: str | None
    lot_step: str | None
    listed_at: datetime | None


class SourceFileView(BaseModel):
    path: str
    content_sha256: str
    rows: int


class DatasetManifestView(BaseModel):
    """GET /v1/datasets/{version}: 'what exactly did this use?' (AC2)."""

    dataset_version: str
    venue: str
    market_type: str
    instruments: list[str]
    timeframes: list[str]
    start_time: datetime
    end_time: datetime
    source_files: list[SourceFileView]
    row_counts: dict[str, int]
    missing_ranges: list[dict[str, Any]]
    quality_summary: dict[str, Any]
    dataset_schema_version: str
    bar_semantics_version: str
    timezone_policy_version: str
    instrument_metadata_version: str
    created_at: datetime


class QualityView(BaseModel):
    instrument_id: UUID
    timeframe: str
    state: str
    completeness: float
    detail: dict[str, Any]
    computed_at: datetime


class GapView(BaseModel):
    gap_id: UUID
    instrument_id: UUID
    timeframe: str
    exclusion_start: datetime
    exclusion_end: datetime
    status: str
    kind: str
    source: str
    detected_at: datetime
    repaired_at: datetime | None
    repaired_dataset_version: str | None


class HealthView(BaseModel):
    """GET /v1/data/health: latest quality per series + exclusions still active."""

    series: list[QualityView]
    exclusions: list[GapView]


# --------------------------------------------------------------------------
# Mappers
# --------------------------------------------------------------------------


def _job_view(job: JobRecord) -> JobView:
    return JobView(
        job_id=job.job_id,
        kind=job.kind.value,
        status=job.status.value,
        progress=job.progress,
        result_ref=job.result_ref,
        params=job.params,
        logs=job.logs,
        error=job.error,
        timestamps=JobTimestamps(
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        ),
    )


def _instrument_view(item: Instrument) -> InstrumentView:
    return InstrumentView(
        instrument_id=item.instrument_id,
        venue=item.venue,
        market_type=item.market_type.value,
        asset=item.asset,
        quote=item.quote,
        native_symbol=item.native_symbol,
        tick_size=None if item.tick_size is None else str(item.tick_size),
        lot_step=None if item.lot_step is None else str(item.lot_step),
        listed_at=item.listed_at,
    )


def _manifest_view(manifest: DatasetManifest) -> DatasetManifestView:
    return DatasetManifestView(
        dataset_version=manifest.dataset_version,
        venue=manifest.venue,
        market_type=manifest.market_type,
        instruments=list(manifest.instruments),
        timeframes=list(manifest.timeframes),
        start_time=manifest.start_time,
        end_time=manifest.end_time,
        source_files=[
            SourceFileView(path=f.path, content_sha256=f.content_sha256, rows=f.rows)
            for f in manifest.source_files
        ],
        row_counts=dict(manifest.row_counts),
        missing_ranges=list(manifest.missing_ranges),
        quality_summary=dict(manifest.quality_summary),
        dataset_schema_version=manifest.dataset_schema_version,
        bar_semantics_version=manifest.bar_semantics_version,
        timezone_policy_version=manifest.timezone_policy_version,
        instrument_metadata_version=manifest.instrument_metadata_version,
        created_at=manifest.created_at,
    )


def _quality_view(record: QualityRecord) -> QualityView:
    return QualityView(
        instrument_id=record.instrument_id,
        timeframe=record.timeframe,
        state=record.state.value,
        completeness=record.completeness,
        detail=record.detail,
        computed_at=record.computed_at,
    )


def _gap_view(gap: GapRow) -> GapView:
    return GapView(
        gap_id=gap.gap_id,
        instrument_id=gap.instrument_id,
        timeframe=gap.timeframe,
        exclusion_start=gap.exclusion_start,
        exclusion_end=gap.exclusion_end,
        status=gap.status,
        kind=gap.kind,
        source=gap.source,
        detected_at=gap.detected_at,
        repaired_at=gap.repaired_at,
        repaired_dataset_version=gap.repaired_dataset_version,
    )


# --------------------------------------------------------------------------
# Fail-closed helpers (PD-10 / PD-11)
# --------------------------------------------------------------------------


def _parse_market_type(raw: str | None) -> MarketType | None:
    if raw is None:
        return None
    try:
        return MarketType(raw)
    except ValueError as exc:
        raise ValidationError("unknown market_type", {"market_type": raw}) from exc


def _require_market_type(raw: str) -> MarketType:
    try:
        return MarketType(raw)
    except ValueError as exc:
        raise ValidationError("unknown market_type", {"market_type": raw}) from exc


def _parse_timeframe(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        return parse_timeframe(raw).value
    except UnknownTimeframeError as exc:
        raise ValidationError("unknown timeframe", {"timeframe": raw}) from exc


def _resolve_budget_bytes(requested: float | None) -> int:
    """PD-11: the request, else ``config/defaults.yaml[data.storage_budget_gb]``."""
    gigabytes = (
        requested if requested is not None else float(load_defaults()["data"]["storage_budget_gb"])
    )
    if gigabytes <= 0:
        raise ValidationError(
            "storage_budget_gb must be positive", {"storage_budget_gb": gigabytes}
        )
    return int(gigabytes * GB)


def _data_dir() -> Path:
    return Path(get_settings().data_dir)


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get("/instruments", response_model=list[InstrumentView], tags=["data"])
async def instruments(
    venue: str | None = None,
    market_type: str | None = None,
    asset: str | None = None,
) -> list[InstrumentView]:
    """The instrument registry (PD-12: scopes name these explicitly)."""
    parsed_market = _parse_market_type(market_type)
    with connection_scope() as conn:
        rows = list_instruments(conn, venue=venue, market_type=parsed_market, asset=asset)
    return [_instrument_view(row) for row in rows]


@router.post("/data/ingest", response_model=JobQueued, status_code=202, tags=["data"])
async def data_ingest(body: IngestRequest, background: BackgroundTasks) -> JobQueued:
    """Queue an ingest/backfill job; execution happens in the background task."""
    parsed_market = _require_market_type(body.market_type)
    if parsed_market is MarketType.PERPETUAL:
        raise NotImplementedMarketError(
            "perpetual data is not served before M10", {"market_type": "PERPETUAL"}
        )
    if body.venue != "BINANCE":
        raise NotImplementedMarketError("M1 ingests only the BINANCE venue", {"venue": body.venue})
    kind = resolve_job_kind(body.timeframes)
    budget_bytes = _resolve_budget_bytes(body.storage_budget_gb)
    ensure_storage_budget(_data_dir(), budget_bytes)
    params: dict[str, Any] = {
        "venue": body.venue,
        "market_type": parsed_market.value,
        "instrument_ids": list(body.instrument_ids),
        "timeframes": list(body.timeframes),
        "start_ms": round(body.from_.timestamp() * 1000),
        "end_ms": round(body.to.timestamp() * 1000),
        "storage_budget_bytes": budget_bytes,
    }
    with connection_scope() as conn:
        job = enqueue_job(conn, kind=kind, params=params)
    background.add_task(run_job, job.job_id)
    return JobQueued(job_id=job.job_id, status="queued")


@router.post("/data/repair", response_model=JobQueued, status_code=202, tags=["data"])
async def data_repair(body: RepairRequest, background: BackgroundTasks) -> JobQueued:
    """Queue a refetch of one stored gap (section 4.6: refetch, re-assess, re-derive)."""
    budget_bytes = _resolve_budget_bytes(body.storage_budget_gb)
    with connection_scope() as conn:
        gap = get_gap(conn, body.gap_id)
        if gap is None:
            raise NotFoundError("gap is not registered", {"gap_id": str(body.gap_id)})
        instrument = get_instrument(conn, gap.instrument_id)
        if instrument is None:
            raise NotFoundError(
                "gap's instrument is not registered",
                {"instrument_id": str(gap.instrument_id)},
            )
        ensure_storage_budget(_data_dir(), budget_bytes)
        job = enqueue_job(
            conn,
            kind=JobKind.REPAIR,
            params={
                "gap_id": str(body.gap_id),
                "storage_budget_bytes": budget_bytes,
                "venue": instrument.venue,
                "market_type": instrument.market_type.value,
            },
        )
    background.add_task(run_job, job.job_id)
    return JobQueued(job_id=job.job_id, status="queued")


@router.get("/data/health", response_model=HealthView, tags=["data"])
async def data_health(
    market_type: str | None = None,
    instrument_id: UUID | None = None,
    timeframe: str | None = None,
) -> HealthView:
    """Latest quality per (instrument, timeframe) + the exclusions still active."""
    parsed_market = _parse_market_type(market_type)
    if parsed_market is MarketType.PERPETUAL:
        raise NotImplementedMarketError(
            "perpetual health is not served before M10", {"market_type": "PERPETUAL"}
        )
    parsed_tf = _parse_timeframe(timeframe)
    with connection_scope() as conn:
        series = latest_assessments(conn, instrument_id=instrument_id, timeframe=parsed_tf)
        exclusions = load_exclusions(
            conn,
            instrument_id=instrument_id,
            timeframe=parsed_tf,
            active_only=True,
        )
    return HealthView(
        series=[_quality_view(row) for row in series],
        exclusions=[_gap_view(row) for row in exclusions],
    )


@router.get("/data/gaps", response_model=list[GapView], tags=["data"])
async def data_gaps(
    instrument_id: UUID | None = None,
    timeframe: str | None = None,
    status: str | None = None,
) -> list[GapView]:
    """Every stored exclusion interval (section 4.6: reports list all of them)."""
    normalized_status: str | None = None
    if status is not None:
        normalized_status = status.upper()
        if normalized_status not in _GAP_STATUSES:
            raise ValidationError(
                "unknown gap status",
                {"status": status, "allowed": list(_GAP_STATUSES)},
            )
    parsed_tf = _parse_timeframe(timeframe)
    with connection_scope() as conn:
        rows = load_exclusions(
            conn,
            instrument_id=instrument_id,
            timeframe=parsed_tf,
            status=normalized_status,
        )
    return [_gap_view(row) for row in rows]


@router.get("/jobs/{job_id}", response_model=JobView, tags=["data"])
async def job_view(job_id: UUID) -> JobView:
    """One queued/running/finished job (spec section 11)."""
    with connection_scope() as conn:
        job = get_job(conn, job_id)
    if job is None:
        raise NotFoundError("job is not registered", {"job_id": str(job_id)})
    return _job_view(job)


@router.get("/datasets/{dataset_version}", response_model=DatasetManifestView, tags=["data"])
async def dataset_view(dataset_version: str) -> DatasetManifestView:
    """The stored manifest: source files, hashes, row counts, version fields (AC2)."""
    with connection_scope() as conn:
        manifest = get_manifest(conn, dataset_version)
    if manifest is None:
        raise NotFoundError(
            "dataset_version is not registered", {"dataset_version": dataset_version}
        )
    return _manifest_view(manifest)
