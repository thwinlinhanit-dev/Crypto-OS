"""Append-only result pattern (PD-13): ``research.stats_result``.

Every row is identified by ``(ref_id, dataset_version, engine_version, code_version)``
and is never overwritten: only ``locked`` may change, and nothing is deleted. A
recalculation inserts a new ``ref_id`` carrying ``supersedes``. The database
enforces this with triggers (see infra/migrations/versions/0001_*.py); these
assertions exist so the intent is testable without reading SQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, func
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
