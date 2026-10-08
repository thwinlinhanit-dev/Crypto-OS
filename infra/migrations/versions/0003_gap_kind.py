"""M1 (carry-over): persist the exclusion ``kind`` on ``system.gap``.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07

Section 4.6 defines two exclusion kinds (gap vs invalid) and the design note
(sections 6.1/7) reads them back from ``system.gap`` rows - section 7 passes
"the active system.gap rows (kind GAP/INVALID)" into the weekly resampler.
Migration 0002 stored the interval without the kind, so the value was lost on
persistence. ADDITION (carried in docs/DECISIONS.md): a spec concept that the
DDL text omits gets its column here rather than being re-invented at read time.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gap",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="gap"),
        schema="system",
    )
    op.create_check_constraint(
        "ck_gap_kind",
        "gap",
        "kind IN ('gap','invalid')",
        schema="system",
    )


def downgrade() -> None:
    op.drop_constraint("ck_gap_kind", "gap", schema="system", type_="CHECK")
    op.drop_column("gap", "kind", schema="system")
