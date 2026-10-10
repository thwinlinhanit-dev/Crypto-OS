"""M7: validation runs (spec section 10, 9.5).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-10

``validation.run`` transcribed from spec section 10: one run per
(backtest_id, level, kind) request; the row stores the 9.5 provenance
(strategy_version hash, dataset_version, engine_version, code_version,
resolved_params, universe, data_scope, fill_model, seed) plus the level
results and the overall status list (9.4).

The 0001-style ``locked`` flag applies (D-050): a run referenced by a
frozen experiment rejects UPDATE/DELETE once locked.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VALIDATION_KINDS = ("edge", "oos", "walk_forward", "clustering_sensitivity",
                    "multiple_testing", "random_baseline", "param_stability",
                    "trade_dependency", "concentration", "regime_split",
                    "cost_stress", "delay_stress", "gap_risk", "monte_carlo",
                    "liquidity_funding")

RUN_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION validation.assert_run_immutable() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: validation.run is append-only; DELETE rejected on %.',
            TG_TABLE_NAME;
    END IF;
    IF NEW.locked AND OLD.locked THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: validation.run is locked; UPDATE rejected on %.',
            TG_TABLE_NAME;
    END IF;
    IF NEW.locked IS DISTINCT FROM OLD.locked AND NOT NEW.locked THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: validation.run.locked cannot be unset';
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "run",
        sa.Column("run_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("backtest_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("params", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["backtest_id"], ["strategy.backtest.backtest_id"], name="fk_run_backtest"
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"], ["research.experiment.experiment_id"], name="fk_run_experiment"
        ),
        sa.CheckConstraint("level IN (1, 2, 3)", name="ck_run_level"),
        schema="validation",
    )
    op.create_index("ix_run_backtest", "run", ["backtest_id"], schema="validation")
    op.create_index("ix_run_created_at", "run", ["created_at"], schema="validation")

    op.execute(RUN_APPEND_ONLY_FUNCTION)
    op.execute(
        "CREATE TRIGGER run_append_only BEFORE UPDATE OR DELETE ON validation.run"
        " FOR EACH ROW EXECUTE FUNCTION validation.assert_run_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS run_append_only ON validation.run")
    op.execute("DROP FUNCTION IF EXISTS validation.assert_run_immutable()")
    op.drop_table("run", schema="validation")
