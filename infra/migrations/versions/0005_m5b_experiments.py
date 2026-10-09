"""M5b: experiment families, variants, experiments (spec section 10, 8.4, 8.5).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10

Tables transcribed from spec section 10. Deliberate choices beyond the literal
DDL (recorded in specs/design/m5b_experiments.md):

- ``research.experiment``: ``data_scope``/``selection_method`` CHECKs are in
  the spec's DDL text itself; ``ck_experiment_status`` ADDITION restates the
  8.4/11 status vocabulary so a typo cannot become a silent state.
- ``research.variant`` is append-only (the DDL's own comment): the trigger
  rejects DELETE outright and UPDATE of any column except ``selected`` (the
  selection flag), mirroring PD-13's "only lock/freeze flags may change" and
  the 0001 ``stats_result`` pattern. How would a sweep otherwise record which
  cell it picked without editing rows.
- ``research.experiment`` rejects UPDATE and DELETE outright once
  ``frozen`` (spec 8.5: frozen experiments cannot be updated, deleted, or
  change their referenced results). Unfrozen rows stay patchable - the API
  PATCH is what enforces which fields may move.
- ``research.experiment_family`` rejects DELETE once variants or experiments
  reference it (a family is the record of a search); the runner maintains
  ``variant_count`` in the same transaction as each variant insert.

Downgrade drops triggers, functions, then tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Status vocabulary for experiments (8.4 lifecycle + 11 endpoints). ADDITION.
EXPERIMENT_STATUSES = (
    "IDEA",
    "RUNNING",
    "PROMISING",
    "SUPPORTED",
    "WEAK",
    "REJECTED",
    "VALIDATED",
)

VARIANT_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION research.assert_variant_append_only() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: research.variant is append-only; DELETE rejected on %.',
            TG_TABLE_NAME;
    END IF;

    IF OLD.variant_id IS DISTINCT FROM NEW.variant_id
       OR OLD.family_id  IS DISTINCT FROM NEW.family_id
       OR OLD.experiment_id IS DISTINCT FROM NEW.experiment_id
       OR OLD.params_hash IS DISTINCT FROM NEW.params_hash
       OR OLD.resolved_params IS DISTINCT FROM NEW.resolved_params
       OR OLD.result_ref IS DISTINCT FROM NEW.result_ref
       OR OLD.created_at IS DISTINCT FROM NEW.created_at
    THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: research.%.variant is append-only; only the selected flag may change',
            TG_TABLE_NAME;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
"""

EXPERIMENT_FROZEN_FUNCTION = """
CREATE OR REPLACE FUNCTION research.assert_experiment_unfrozen() RETURNS trigger AS $fn$
BEGIN
    IF OLD.frozen THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: research.experiment is frozen; %. rejected (spec 8.5)',
            TG_OP;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
"""

FAMILY_IN_USE_FUNCTION = """
CREATE OR REPLACE FUNCTION research.assert_family_deletable() RETURNS trigger AS $fn$
DECLARE
    variants bigint;
    experiments bigint;
BEGIN
    SELECT COUNT(*) INTO variants FROM research.variant WHERE family_id = OLD.family_id;
    SELECT COUNT(*) INTO experiments FROM research.experiment WHERE family_id = OLD.family_id;
    IF variants > 0 OR experiments > 0 THEN
        RAISE EXCEPTION 'IMMUTABLE_RESULT: research.experiment_family is referenced (% variants, % experiments); DELETE rejected',
            variants, experiments;
    END IF;
    RETURN OLD;
END;
$fn$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "experiment_family",
        sa.Column("family_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("hypothesis_ids", JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "search_space",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("hypothesis_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("variant_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="research",
    )
    op.create_index(
        "ix_experiment_family_created_at",
        "experiment_family",
        ["created_at"],
        schema="research",
    )

    op.create_table(
        "variant",
        sa.Column("variant_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("params_hash", sa.Text(), nullable=False),
        sa.Column(
            "resolved_params",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result_ref", sa.Uuid(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["family_id"],
            ["research.experiment_family.family_id"],
            name="fk_variant_family",
        ),
        schema="research",
    )
    op.create_index("ix_variant_family", "variant", ["family_id"], schema="research")

    op.create_table(
        "experiment",
        sa.Column("experiment_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column(
            "assessment",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("universe", JSONB(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("data_scope", sa.Text(), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("dataset_schema_version", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("code_version", sa.Text(), nullable=False),
        sa.Column("detector_refs", JSONB(), nullable=False),
        sa.Column("resolved_params", JSONB(), nullable=False),
        sa.Column("selection_method", sa.Text(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column(
            "refs",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "result_hashes",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "data_scope IN ('DEVELOPMENT','HOLDOUT')",
            name="ck_experiment_data_scope",
        ),
        sa.CheckConstraint(
            "selection_method IN ('PRE_SPECIFIED','SWEEP_BEST','MANUAL_CHOICE')",
            name="ck_experiment_selection_method",
        ),
        # ADDITION: restates the 8.4/11 status vocabulary.
        sa.CheckConstraint(
            "status IN ('IDEA','RUNNING','PROMISING','SUPPORTED','WEAK','REJECTED','VALIDATED')",
            name="ck_experiment_status",
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"],
            ["research.hypothesis.hyp_id"],
            name="fk_experiment_hypothesis",
        ),
        sa.ForeignKeyConstraint(
            ["family_id"],
            ["research.experiment_family.family_id"],
            name="fk_experiment_family",
        ),
        schema="research",
    )
    op.create_index(
        "ix_experiment_hypothesis", "experiment", ["hypothesis_id"], schema="research"
    )
    op.create_index("ix_experiment_family", "experiment", ["family_id"], schema="research")
    op.create_index("ix_experiment_frozen", "experiment", ["frozen"], schema="research")

    op.execute(VARIANT_APPEND_ONLY_FUNCTION)
    op.execute(EXPERIMENT_FROZEN_FUNCTION)
    op.execute(FAMILY_IN_USE_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER variant_append_only
            BEFORE UPDATE OR DELETE ON research.variant
            FOR EACH ROW EXECUTE FUNCTION research.assert_variant_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER experiment_frozen
            BEFORE UPDATE OR DELETE ON research.experiment
            FOR EACH ROW EXECUTE FUNCTION research.assert_experiment_unfrozen();
        """
    )
    op.execute(
        """
        CREATE TRIGGER experiment_family_in_use
            BEFORE DELETE ON research.experiment_family
            FOR EACH ROW EXECUTE FUNCTION research.assert_family_deletable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS experiment_family_in_use ON research.experiment_family")
    op.execute("DROP TRIGGER IF EXISTS experiment_frozen ON research.experiment")
    op.execute("DROP TRIGGER IF EXISTS variant_append_only ON research.variant")
    op.execute("DROP FUNCTION IF EXISTS research.assert_family_deletable()")
    op.execute("DROP FUNCTION IF EXISTS research.assert_experiment_unfrozen()")
    op.execute("DROP FUNCTION IF EXISTS research.assert_variant_append_only()")
    op.drop_table("experiment", schema="research")
    op.drop_table("variant", schema="research")
    op.drop_table("experiment_family", schema="research")
