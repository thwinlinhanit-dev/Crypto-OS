"""M6: strategy versions, backtests, trades (spec section 10, section 7).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-10

Tables transcribed from spec section 10:

- ``strategy.strategy_version`` (PK strategy_id + version): the versioned,
  content-hashed definition JSON (7.1). Edits create a new version, never an
  in-place mutation.
- ``strategy.backtest`` (spec 10): one run; ``experiment_id`` NOT NULL (D-041
  - every backtest belongs to an experiment); ``locked`` follows the 8.5
  freeze pattern, so a backtest referenced by a frozen experiment rejects
  UPDATE/DELETE once locked (the same trigger shape as 0001's stats_result,
  extended to strategy.backtest here).
- ``strategy.trade`` (PK backtest_id + trade_no): the 7.5 trade record,
  append-only (PD-13). Deletion would destroy the trade list a frozen
  experiment's hash was computed over.

Downgrade drops triggers, functions, then tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TSTZRANGE

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Backtest status vocabulary (M6): the async job lifecycle of §11.
BACKTEST_STATUSES = ("queued", "running", "succeeded", "failed")

#: 7.3 fill models (R8: REALISTIC_INTRABAR reserved, never stored).
FILL_MODELS = ("PARITY", "REALISTIC_OHLC")
BACKTEST_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION strategy.assert_backtest_immutable() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: strategy.backtest is append-only; DELETE rejected on %.',
            TG_TABLE_NAME;
    END IF;
    IF NEW.locked AND OLD.locked THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: strategy.backtest is locked; UPDATE rejected on %.',
            TG_TABLE_NAME;
    END IF;
    -- locked is a one-way flag (PD-13: only lock/freeze flags may change).
    IF NEW.locked IS DISTINCT FROM OLD.locked AND NOT NEW.locked THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: strategy.backtest.locked cannot be unset';
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
"""

TRADE_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION strategy.assert_trade_append_only() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'IMMUTABLE_RESULT: strategy.trade is append-only; % rejected on %.',
        TG_OP, TG_TABLE_NAME;
END;
$fn$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "strategy_version",
        sa.Column("strategy_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("definition", JSONB(), nullable=False),
        sa.Column("definition_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("strategy_id", "version", name="pk_strategy_version"),
        schema="strategy",
    )

    op.create_table(
        "backtest",
        sa.Column("backtest_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("universe", JSONB(), nullable=False),
        sa.Column("period", TSTZRANGE(), nullable=False),
        sa.Column("data_scope", sa.Text(), nullable=False),
        sa.Column("fill_model", sa.Text(), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("dataset_schema_version", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("code_version", sa.Text(), nullable=False),
        sa.Column(
            "resolved_params",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column(
            "metrics",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "skipped",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("supersedes", sa.Uuid(), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["research.experiment.experiment_id"],
            name="fk_backtest_experiment",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id", "version"],
            ["strategy.strategy_version.strategy_id", "strategy.strategy_version.version"],
            name="fk_backtest_strategy_version",
        ),
        sa.CheckConstraint(
            "data_scope IN ('DEVELOPMENT','HOLDOUT')", name="ck_backtest_data_scope"
        ),
        sa.CheckConstraint(
            "fill_model IN ('PARITY','REALISTIC_OHLC')", name="ck_backtest_fill_model"
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed')", name="ck_backtest_status"
        ),
        schema="strategy",
    )
    op.create_index(
        "ix_backtest_experiment", "backtest", ["experiment_id"], schema="strategy"
    )
    op.create_index("ix_backtest_created_at", "backtest", ["created_at"], schema="strategy")

    op.create_table(
        "trade",
        sa.Column("backtest_id", sa.Uuid(), nullable=False),
        sa.Column("trade_no", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("signal_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fill_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_signal_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_decision_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_fill_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_price", sa.Numeric(), nullable=False),
        sa.Column("exit_price", sa.Numeric(), nullable=True),
        sa.Column("qty", sa.Numeric(), nullable=False),
        sa.Column("fees", sa.Numeric(), nullable=False),
        sa.Column("pnl", sa.Numeric(), nullable=True),
        sa.Column("pnl_pct", sa.Numeric(), nullable=True),
        sa.Column("r_multiple", sa.Numeric(), nullable=True),
        sa.Column("mfe_r", sa.Numeric(), nullable=True),
        sa.Column("mae_r", sa.Numeric(), nullable=True),
        sa.Column("bars_held", sa.Integer(), nullable=True),
        sa.Column("entry_tag", sa.Text(), nullable=False),
        sa.Column("exit_reason", sa.Text(), nullable=True),
        sa.Column("data_gap_during_hold", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("straddles_boundary", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.ForeignKeyConstraint(
            ["backtest_id"],
            ["strategy.backtest.backtest_id"],
            name="fk_trade_backtest",
        ),
        sa.PrimaryKeyConstraint("backtest_id", "trade_no", name="pk_trade"),
        schema="strategy",
    )
    op.create_index("ix_trade_instrument", "trade", ["instrument_id"], schema="strategy")

    op.execute(BACKTEST_APPEND_ONLY_FUNCTION)
    op.execute(TRADE_APPEND_ONLY_FUNCTION)
    op.execute(
        "CREATE TRIGGER backtest_append_only BEFORE UPDATE OR DELETE ON strategy.backtest"
        " FOR EACH ROW EXECUTE FUNCTION strategy.assert_backtest_immutable()"
    )
    op.execute(
        "CREATE TRIGGER trade_append_only BEFORE UPDATE OR DELETE ON strategy.trade"
        " FOR EACH ROW EXECUTE FUNCTION strategy.assert_trade_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trade_append_only ON strategy.trade")
    op.execute("DROP TRIGGER IF EXISTS backtest_append_only ON strategy.backtest")
    op.execute("DROP FUNCTION IF EXISTS strategy.assert_trade_append_only()")
    op.execute("DROP FUNCTION IF EXISTS strategy.assert_backtest_immutable()")
    op.drop_table("trade", schema="strategy")
    op.drop_table("backtest", schema="strategy")
    op.drop_table("strategy_version", schema="strategy")
