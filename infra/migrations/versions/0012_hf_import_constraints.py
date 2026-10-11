"""0012 — the HF idempotent-import constraints (backup/recovery, D-079).

The backup/recovery round-trip (Phase 8) imports the HF tables idempotently:
``ON CONFLICT DO NOTHING`` requires a UNIQUE key to conflict on. Migration
0011 gave ``hypothesis_version`` one (hf_version_unique) but left
``hypothesis_relationship`` and ``research_ledger`` with identity PKs only,
so a re-import would duplicate every evidence edge and every ledger row.

This adds the natural keys:
- hypothesis_relationship: (from, to, relationship) - the same edge declared
  twice is one edge;
- research_ledger: (family_id, hypothesis_id) - a hypothesis joins a testing
  family once; a re-declared plan updates in place (the plan is declared
  BEFORE the analysis, HF-005).
"""

from __future__ import annotations

from typing import Any

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels: Any = None
depends_on: Any = None


def upgrade() -> None:
    op.create_unique_constraint(
        "hf_relationship_unique",
        "hypothesis_relationship",
        ["from_hypothesis_id", "to_hypothesis_id", "relationship"],
        schema="research",
    )
    op.create_unique_constraint(
        "research_ledger_unique",
        "research_ledger",
        ["family_id", "hypothesis_id"],
        schema="research",
    )


def downgrade() -> None:
    op.drop_constraint(
        "research_ledger_unique", "research_ledger", schema="research", type_="unique"
    )
    op.drop_constraint(
        "hf_relationship_unique", "hypothesis_relationship", schema="research", type_="unique"
    )
