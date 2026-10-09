"""M5: research tables - hypothesis registry, notebook notes, event store (spec section 10).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09

Tables are transcribed from spec section 10 verbatim (design note
specs/design/m5_stats.md section 8). Deliberate choices beyond the literal DDL:

- ``research.hypothesis``: title/statement/status/criteria/preregistered_at
  are NOT NULL (creation always writes them, design section 6.2);
  holdout_opened_at/holdout_result/autopsy stay nullable (written later).
  ADDITION: ``ck_hypothesis_status`` restates the 8.4 status enum
  (IDEA/TESTING/PROMISING/SUPPORTED/WEAK/REJECTED/VALIDATED) so a typo cannot
  become a silent state (same precedent as 0002's CHECKs).
- ``research.event``: every column NOT NULL (events are content-addressed
  facts; the detector always emits complete rows). ADDITION:
  ``ck_event_direction`` restates the 6.2 direction vocabulary (LONG/SHORT).
- ``research.note``: title/body_md NOT NULL; chart_state nullable (evidence
  saves may carry no chart state); experiment_id NULL until M5b.
- ``research.event_outcome``: exactly section 10 (PK + outcome columns).

Triggers (PD-13): ``event``/``event_outcome`` are INSERT-only (UPDATE and
DELETE rejected outright - observations are never edited in place, PD-7);
``hypothesis`` rejects UPDATE/DELETE only when ``OLD.locked`` (spec line 501:
unfrozen hypotheses stay patchable, locked rows are frozen). Notes are not
results: no trigger (DELETE allowed, design section 7).

Downgrade drops triggers, functions, then tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION research.assert_event_immutable() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'IMMUTABLE_RESULT: research.% is insert-only; % rejected (PD-13)',
        TG_TABLE_NAME, TG_OP;
    RETURN NULL;
END;
$fn$ LANGUAGE plpgsql;
"""

HYPOTHESIS_LOCKED_FUNCTION = """
CREATE OR REPLACE FUNCTION research.assert_hypothesis_locked() RETURNS trigger AS $fn$
BEGIN
    IF OLD.locked THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: research.% row is locked; % rejected (PD-13)',
            TG_TABLE_NAME, TG_OP;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "hypothesis",
        sa.Column("hyp_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("criteria", JSONB(), nullable=False),
        sa.Column("preregistered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("holdout_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("holdout_result", JSONB(), nullable=True),
        sa.Column("autopsy", sa.Text(), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # ADDITION: restates spec 8.4's status enum so a typo cannot become a state.
        sa.CheckConstraint(
            "status IN ('IDEA','TESTING','PROMISING','SUPPORTED','WEAK','REJECTED','VALIDATED')",
            name="ck_hypothesis_status",
        ),
        schema="research",
    )
    op.create_index("ix_hypothesis_status", "hypothesis", ["status"], schema="research")

    op.create_table(
        "note",
        sa.Column("note_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("chart_state", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="research",
    )
    op.create_index("ix_note_created_at", "note", ["created_at"], schema="research")

    op.create_table(
        "event",
        sa.Column("event_id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("detector_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("params_hash", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("ref_price", sa.Numeric(), nullable=False),
        sa.Column("invalidation_price", sa.Numeric(), nullable=False),
        sa.Column("regime", JSONB(), nullable=False),
        sa.Column("features", JSONB(), nullable=False),
        sa.Column("trigger", JSONB(), nullable=False),
        sa.Column("lookback_gap", sa.Boolean(), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "detector_id",
            "event_type",
            "version",
            "params_hash",
            "instrument_id",
            "timeframe",
            "event_time",
            name="uq_event_identity",
        ),
        # ADDITION: restates spec 6.2's direction vocabulary.
        sa.CheckConstraint("direction IN ('LONG','SHORT')", name="ck_event_direction"),
        schema="research",
    )
    op.create_index(
        "ix_event_detector_type_time",
        "event",
        ["detector_id", "event_type", "event_time"],
        schema="research",
    )

    op.create_table(
        "event_outcome",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("fwd_ret_pct", sa.Numeric(), nullable=True),
        sa.Column("fwd_ret_r", sa.Numeric(), nullable=True),
        sa.Column("mfe_r", sa.Numeric(), nullable=True),
        sa.Column("mae_r", sa.Numeric(), nullable=True),
        sa.Column("bars_to_mfe", sa.Integer(), nullable=True),
        sa.Column("bars_to_mae", sa.Integer(), nullable=True),
        sa.Column("hit_invalidation", sa.Boolean(), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("event_id", "horizon", name="pk_event_outcome"),
        schema="research",
    )

    op.execute(EVENT_IMMUTABLE_FUNCTION)
    op.execute(HYPOTHESIS_LOCKED_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER event_insert_only
            BEFORE UPDATE OR DELETE ON research.event
            FOR EACH ROW EXECUTE FUNCTION research.assert_event_immutable();
        """
    )
    op.execute(
        """
        CREATE TRIGGER event_outcome_insert_only
            BEFORE UPDATE OR DELETE ON research.event_outcome
            FOR EACH ROW EXECUTE FUNCTION research.assert_event_immutable();
        """
    )
    op.execute(
        """
        CREATE TRIGGER hypothesis_locked
            BEFORE UPDATE OR DELETE ON research.hypothesis
            FOR EACH ROW EXECUTE FUNCTION research.assert_hypothesis_locked();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS hypothesis_locked ON research.hypothesis")
    op.execute("DROP TRIGGER IF EXISTS event_outcome_insert_only ON research.event_outcome")
    op.execute("DROP TRIGGER IF EXISTS event_insert_only ON research.event")
    op.execute("DROP FUNCTION IF EXISTS research.assert_hypothesis_locked()")
    op.execute("DROP FUNCTION IF EXISTS research.assert_event_immutable()")
    op.drop_table("event_outcome", schema="research")
    op.drop_table("event", schema="research")
    op.drop_table("note", schema="research")
    op.drop_table("hypothesis", schema="research")
