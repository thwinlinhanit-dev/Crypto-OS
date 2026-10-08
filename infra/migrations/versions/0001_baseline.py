"""baseline: schemas, research.stats_result, append-only triggers (PD-13)

Revision ID: 0001
Revises:
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS: tuple[str, ...] = ("market", "research", "strategy", "validation", "system", "agents")

APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION research.assert_result_append_only() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: research.% is append-only; DELETE rejected (PD-13)',
            TG_TABLE_NAME;
    END IF;

    IF OLD.locked THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: research.% row is locked; UPDATE rejected (PD-13)',
            TG_TABLE_NAME;
    END IF;

    IF NEW.ref_id              IS DISTINCT FROM OLD.ref_id
       OR NEW.experiment_id    IS DISTINCT FROM OLD.experiment_id
       OR NEW."filter"         IS DISTINCT FROM OLD."filter"
       OR NEW.data_scope       IS DISTINCT FROM OLD.data_scope
       OR NEW.dataset_version  IS DISTINCT FROM OLD.dataset_version
       OR NEW.dataset_schema_version IS DISTINCT FROM OLD.dataset_schema_version
       OR NEW.engine_version   IS DISTINCT FROM OLD.engine_version
       OR NEW.code_version     IS DISTINCT FROM OLD.code_version
       OR NEW.resolved_params  IS DISTINCT FROM OLD.resolved_params
       OR NEW.result           IS DISTINCT FROM OLD.result
       OR NEW.seed             IS DISTINCT FROM OLD.seed
       OR NEW.supersedes       IS DISTINCT FROM OLD.supersedes
       OR NEW.created_at       IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: research.% is append-only; only the locked flag may change (PD-13)',
            TG_TABLE_NAME;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
"""

APPEND_ONLY_TRIGGER = """
CREATE TRIGGER stats_result_append_only
    BEFORE UPDATE OR DELETE ON research.stats_result
    FOR EACH ROW EXECUTE FUNCTION research.assert_result_append_only();
"""


def upgrade() -> None:
    for name in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {name}")

    op.create_table(
        "stats_result",
        sa.Column("ref_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("filter", JSONB(), nullable=False),
        sa.Column("data_scope", sa.String(length=16), nullable=False),
        sa.Column("dataset_version", sa.String(length=128), nullable=False),
        sa.Column("dataset_schema_version", sa.String(length=32), nullable=False),
        sa.Column("engine_version", sa.String(length=32), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("resolved_params", JSONB(), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("supersedes", sa.Uuid(), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="research",
    )
    op.create_index("ix_stats_result_created_at", "stats_result", ["created_at"], schema="research")
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(APPEND_ONLY_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS stats_result_append_only ON research.stats_result")
    op.execute("DROP FUNCTION IF EXISTS research.assert_result_append_only()")
    op.drop_table("stats_result", schema="research")
    for name in SCHEMAS:
        op.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
