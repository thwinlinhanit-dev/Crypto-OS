"""Append-only result pattern (PD-13): ``research.stats_result``.

Every row is identified by ``(ref_id, dataset_version, engine_version, code_version)``
and is never overwritten: only ``locked`` may change, and nothing is deleted. A
recalculation inserts a new ``ref_id`` carrying ``supersedes``. The database
enforces this with triggers (see infra/migrations/versions/0001_*.py); these
assertions exist so the intent is testable without reading SQL.

Migration 0004's research tables (spec section 10) are mapped here too:
``research.event`` / ``research.event_outcome`` (INSERT-only observations,
triggers reject UPDATE/DELETE), ``research.hypothesis`` (locked-guard
trigger) and ``research.note`` (notes are not results: DELETE allowed).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for research result tables."""


class StatsResult(Base):
    __tablename__ = "stats_result"
    # Tuple form: table kwargs (schema) ride along without tripping RUF012.
    __table_args__ = ({"schema": "research"},)

    ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_filter: Mapped[dict[str, Any]] = mapped_column("filter", JSONB(), nullable=False)
    data_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    code_version: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_params: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supersedes: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


#: Columns no UPDATE may ever change (PD-13: only lock/freeze flags may change).
IMMUTABLE_RESULT_COLUMNS: tuple[str, ...] = (
    "ref_id",
    "experiment_id",
    "filter",
    "data_scope",
    "dataset_version",
    "dataset_schema_version",
    "engine_version",
    "code_version",
    "resolved_params",
    "result",
    "seed",
    "supersedes",
    "created_at",
)


class ResearchEvent(Base):
    """One content-addressed event fact (spec 10; migration 0004, INSERT-only)."""

    __tablename__ = "event"
    # Tuple form: table kwargs (schema) ride along without tripping RUF012.
    __table_args__ = ({"schema": "research"},)

    event_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    detector_id: Mapped[str] = mapped_column(Text(), nullable=False)
    event_type: Mapped[str] = mapped_column(Text(), nullable=False)
    version: Mapped[str] = mapped_column(Text(), nullable=False)
    params_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    instrument_id: Mapped[str] = mapped_column(Text(), nullable=False)
    timeframe: Mapped[str] = mapped_column(Text(), nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str] = mapped_column(Text(), nullable=False)
    ref_price: Mapped[float] = mapped_column(Numeric(), nullable=False)
    invalidation_price: Mapped[float] = mapped_column(Numeric(), nullable=False)
    regime: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    trigger: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    lookback_gap: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text(), nullable=False)
    engine_version: Mapped[str] = mapped_column(Text(), nullable=False)


class ResearchEventOutcome(Base):
    """One horizon outcome for one event (spec 10; migration 0004, INSERT-only)."""

    __tablename__ = "event_outcome"
    # Tuple form: table kwargs (schema) ride along without tripping RUF012.
    __table_args__ = ({"schema": "research"},)

    event_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    horizon: Mapped[int] = mapped_column(Integer(), primary_key=True)
    fwd_ret_pct: Mapped[float | None] = mapped_column(Numeric(), nullable=True)
    fwd_ret_r: Mapped[float | None] = mapped_column(Numeric(), nullable=True)
    mfe_r: Mapped[float | None] = mapped_column(Numeric(), nullable=True)
    mae_r: Mapped[float | None] = mapped_column(Numeric(), nullable=True)
    bars_to_mfe: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    bars_to_mae: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    hit_invalidation: Mapped[bool | None] = mapped_column(Boolean(), nullable=True)
    complete: Mapped[bool] = mapped_column(Boolean(), nullable=False)


class Hypothesis(Base):
    """A pre-registered research question (spec 8.4/10; migration 0004)."""

    __tablename__ = "hypothesis"
    # Tuple form: table kwargs (schema) ride along without tripping RUF012.
    __table_args__ = ({"schema": "research"},)

    hyp_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(Text(), nullable=False)
    statement: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    preregistered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    holdout_opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    holdout_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(), nullable=True)
    autopsy: Mapped[str | None] = mapped_column(Text(), nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False, server_default="false")


class Note(Base):
    """A notebook note (spec 10/11; migration 0004). Notes are not results:
    DELETE is allowed (PD-13 covers result tables)."""

    __tablename__ = "note"
    # Tuple form: table kwargs (schema) ride along without tripping RUF012.
    __table_args__ = ({"schema": "research"},)

    note_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(Text(), nullable=False)
    body_md: Mapped[str] = mapped_column(Text(), nullable=False)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chart_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
