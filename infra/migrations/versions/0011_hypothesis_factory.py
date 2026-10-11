"""HF Phase 1: the hypothesis data model (spec 6, 18; design note
docs/hypothesis-factory/ARCHITECTURE.md).

``research.hypothesis_version`` holds the formal specification (spec §6);
``research.hypothesis_relationship`` the evidence-graph edges (spec §18.2);
``research.research_ledger`` the multiple-testing families (spec §12 —
declared BEFORE the inferential analysis, HF-005).

The existing ``research.hypothesis`` table stays the backing identity record
(it already carries title/statement/status/criteria); this migration adds the
HF columns + the version/relationship/ledger tables.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

#: The spec §14.2 validation status vocabulary (HF-003, a DB CHECK).
STATUS_VALUES = (
    "NOT_TESTED", "EXPLORATORY", "NEEDS_MORE_DATA", "INVALID_EXPERIMENT",
    "FAILED_STATISTICAL_TEST", "FAILED_ROBUSTNESS", "FAILED_OUT_OF_SAMPLE",
    "PASSED_RESEARCH_VALIDATION", "PAPER_TRADING", "PAPER_TRADING_PASSED",
    "REQUIRES_HUMAN_REVIEW", "ARCHIVED",
)

#: The spec §5 taxonomy (extensible; the DB CHECK is the initial set).
DOMAIN_VALUES = (
    "PRICE_TREND", "DERIVATIVES", "CAPITULATION_RECOVERY", "CROSS_ASSET",
    "ON_CHAIN", "FUNDAMENTAL", "NARRATIVE", "REGIME", "NEGATIVE",
)

#: The spec §4 origin vocabulary (AI is a proposal, never a finding).
ORIGIN_VALUES = ("AI_GENERATED", "PATTERN_DISCOVERY", "RESEARCH_GAP", "HUMAN")

#: The spec §18.2 relationship vocabulary.
RELATIONSHIP_VALUES = (
    "DERIVED_FROM", "REPLICATION_OF", "CONTRADICTS", "REFINES",
    "TESTS_INTERACTION_WITH", "SHARES_FEATURE_WITH", "SUPERSEDES", "INVALIDATES",
)


def upgrade() -> None:
    # The HF columns on the existing hypothesis identity table.
    op.add_column("hypothesis", sa.Column("hf_domain", sa.Text(), nullable=True), schema="research")
    op.add_column("hypothesis", sa.Column("hf_origin", sa.Text(), nullable=True), schema="research")
    op.add_column("hypothesis", sa.Column("hf_status", sa.Text(), server_default="NOT_TESTED", nullable=False), schema="research")
    op.add_column("hypothesis", sa.Column("original_question", sa.Text(), nullable=True), schema="research")
    op.execute(
        "ALTER TABLE research.hypothesis ADD CONSTRAINT hf_hypothesis_domain_check "
        f"CHECK (hf_domain IS NULL OR hf_domain IN ({_sql_list(DOMAIN_VALUES)}))"
    )
    op.execute(
        "ALTER TABLE research.hypothesis ADD CONSTRAINT hf_hypothesis_origin_check "
        f"CHECK (hf_origin IS NULL OR hf_origin IN ({_sql_list(ORIGIN_VALUES)}))"
    )
    op.execute(
        "ALTER TABLE research.hypothesis ADD CONSTRAINT hf_hypothesis_status_check "
        f"CHECK (hf_status IN ({_sql_list(STATUS_VALUES)}))"
    )

    # The formal specification (spec §6): versioned, append-only.
    op.create_table(
        "hypothesis_version",
        sa.Column(
            "version_id",
            sa.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "hypothesis_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("research.hypothesis.hyp_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("mechanism", sa.Text(), nullable=True),
        sa.Column("null_hypothesis", sa.Text(), nullable=False),
        sa.Column("alternative_hypothesis", sa.Text(), nullable=False),
        sa.Column("independent_vars", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("dependent_vars", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("features", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("population", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("event_definition", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("observation_ts_rule", sa.Text(), nullable=False),
        sa.Column("outcome_horizon", sa.Text(), nullable=False),
        sa.Column("expected_direction", sa.Text(), nullable=False),
        sa.Column("baseline", sa.Text(), nullable=True),
        sa.Column("inclusion_criteria", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("exclusion_criteria", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("confounders", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("data_requirements", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("proposed_statistical_method", sa.Text(), nullable=False),
        sa.Column("proposed_robustness_tests", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("falsification_criteria", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("economic_significance_threshold", sa.Numeric(), nullable=True),
        sa.Column("known_limitations", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        schema="research",
    )
    op.create_index(
        "ix_hf_version_hypothesis", "hypothesis_version", ["hypothesis_id"],
        schema="research",
    )
    op.execute(
        "ALTER TABLE research.hypothesis_version"
        " ADD CONSTRAINT hf_version_unique UNIQUE (hypothesis_id, version)"
    )
    op.execute(
        "ALTER TABLE research.hypothesis_version ADD CONSTRAINT hf_version_direction_check "
        "CHECK (expected_direction IN ('HIGHER','LOWER','DIFFERENT'))"
    )

    # The evidence graph (spec §18.2).
    op.create_table(
        "hypothesis_relationship",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column(
            "from_hypothesis_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("research.hypothesis.hyp_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_hypothesis_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("research.hypothesis.hyp_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relationship", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("from_hypothesis_id <> to_hypothesis_id", name="hf_rel_no_self"),
        schema="research",
    )
    op.execute(
        "ALTER TABLE research.hypothesis_relationship ADD CONSTRAINT hf_rel_kind_check "
        f"CHECK (relationship IN ({_sql_list(RELATIONSHIP_VALUES)}))"
    )

    # The research ledger (spec §12): the testing family declared before the
    # inferential analysis (HF-005).
    op.create_table(
        "research_ledger",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column("family_id", sa.Text(), nullable=False),
        sa.Column("hypothesis_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("research.hypothesis.hyp_id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("correction_plan", sa.Text(), nullable=False),
        sa.Column("declared_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="research",
    )
    op.execute(
        "ALTER TABLE research.research_ledger ADD CONSTRAINT hf_ledger_correction_check "
        "CHECK (correction_plan IN ('BENJAMINI_HOCHBERG','BONFERRONI','WHITE_REALITY_CHECK',"
        "'DEFLATED_SHARPE','NONE_DECLARED'))"
    )


def downgrade() -> None:
    op.drop_table("research_ledger", schema="research")
    op.drop_table("hypothesis_relationship", schema="research")
    op.drop_table("hypothesis_version", schema="research")
    op.execute(
        "ALTER TABLE research.hypothesis DROP CONSTRAINT IF EXISTS hf_hypothesis_status_check"
    )
    op.execute(
        "ALTER TABLE research.hypothesis DROP CONSTRAINT IF EXISTS hf_hypothesis_origin_check"
    )
    op.execute(
        "ALTER TABLE research.hypothesis DROP CONSTRAINT IF EXISTS hf_hypothesis_domain_check"
    )
    op.drop_column("hypothesis", "original_question", schema="research")
    op.drop_column("hypothesis", "hf_status", schema="research")
    op.drop_column("hypothesis", "hf_origin", schema="research")
    op.drop_column("hypothesis", "hf_domain", schema="research")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)
