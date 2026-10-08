"""M1: instrument registry, dataset manifest, guardian (gaps/quality), jobs, audit.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07

Columns added beyond the literal DDL of spec section 10 are marked ADDITION and
carried in docs/DECISIONS.md (D-007): ``system.job`` gains ``result_ref`` and
``logs`` because section 11 requires ``GET /v1/jobs/{id}`` to return them.
``market.instrument`` gains CHECKs that restate spec text (section 4.8 market
types, section 4.6 quality states) so a typo cannot become a silent state.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instrument",
        sa.Column("instrument_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("market_type", sa.String(length=16), nullable=False),
        sa.Column("asset", sa.String(length=32), nullable=False),
        sa.Column("quote", sa.String(length=32), nullable=False),
        sa.Column("native_symbol", sa.String(length=48), nullable=False),
        sa.Column("tick_size", sa.Numeric(precision=30, scale=18), nullable=True),
        sa.Column("lot_step", sa.Numeric(precision=30, scale=18), nullable=True),
        sa.Column("listed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("venue", "market_type", "native_symbol", name="uq_instrument_identity"),
        # ADDITION: restates spec 4.8 (SPOT / PERPETUAL identity from M0).
        sa.CheckConstraint("market_type IN ('SPOT','PERPETUAL')", name="ck_instrument_market_type"),
        schema="market",
    )
    op.create_index("ix_instrument_market_type", "instrument", ["market_type"], schema="market")
    op.create_index("ix_instrument_asset", "instrument", ["asset"], schema="market")

    op.create_table(
        "dataset_manifest",
        sa.Column("dataset_version", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("market_type", sa.String(length=16), nullable=False),
        sa.Column("instruments", JSONB(), nullable=False),
        sa.Column("timeframes", JSONB(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_files", JSONB(), nullable=False),
        sa.Column("row_counts", JSONB(), nullable=False),
        sa.Column("missing_ranges", JSONB(), nullable=False),
        sa.Column("quality_summary", JSONB(), nullable=False),
        sa.Column("dataset_schema_version", sa.String(length=32), nullable=False),
        sa.Column("bar_semantics_version", sa.String(length=32), nullable=False),
        sa.Column("timezone_policy_version", sa.String(length=32), nullable=False),
        sa.Column("instrument_metadata_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="market",
    )
    op.create_index("ix_dataset_manifest_created_at", "dataset_manifest", ["created_at"], schema="market")
    op.create_index(
        "ix_dataset_manifest_scope",
        "dataset_manifest",
        ["venue", "market_type"],
        schema="market",
    )

    op.create_table(
        "gap",
        sa.Column("gap_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("exclusion_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exclusion_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("repaired_at", sa.DateTime(timezone=True), nullable=True),
        # ADDITION: an exclusion that can be re-detected after repair must be
        # distinguishable from the original detection (spec 4.6 repair flow).
        sa.Column("repaired_dataset_version", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "status IN ('OPEN','REPAIRED','UNREPAIRABLE')",
            name="ck_gap_status",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["market.instrument.instrument_id"],
            name="fk_gap_instrument",
        ),
        schema="system",
    )
    op.create_index("ix_gap_instrument_timeframe", "gap", ["instrument_id", "timeframe"], schema="system")
    op.create_index("ix_gap_status", "gap", ["status"], schema="system")

    op.create_table(
        "data_quality",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("state", sa.String(length=8), nullable=False),
        sa.Column("completeness", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("detail", JSONB(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # ADDITION: restates spec 4.6's four states.
        sa.CheckConstraint(
            "state IN ('GREEN','YELLOW','ORANGE','RED')",
            name="ck_data_quality_state",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["market.instrument.instrument_id"],
            name="fk_data_quality_instrument",
        ),
        schema="system",
    )
    op.create_index(
        "ix_data_quality_series",
        "data_quality",
        ["instrument_id", "timeframe", "computed_at"],
        schema="system",
    )

    op.create_table(
        "job",
        sa.Column("job_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Numeric(precision=6, scale=3), nullable=False, server_default=sa.text("0")),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # ADDITION (D-007): spec section 11 returns result_ref and logs from
        # GET /v1/jobs/{id}; the section 10 DDL has no columns to hold them.
        sa.Column("result_ref", sa.String(length=128), nullable=True),
        sa.Column("logs", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        schema="system",
    )
    op.create_index("ix_job_status_created", "job", ["status", "created_at"], schema="system")
    op.create_index("ix_job_kind", "job", ["kind"], schema="system")

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=128), nullable=False),
        sa.Column("detail", JSONB(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="system",
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"], schema="system")


def downgrade() -> None:
    op.drop_table("audit_log", schema="system")
    op.drop_table("job", schema="system")
    op.drop_table("data_quality", schema="system")
    op.drop_table("gap", schema="system")
    op.drop_table("dataset_manifest", schema="market")
    op.drop_table("instrument", schema="market")
