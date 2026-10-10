"""M8: watchlists (spec section 10).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-10

``market.watchlist`` + ``market.watchlist_item`` transcribed from spec
section 10. Items are (watchlist, instrument) pairs (D-057), so one
instrument can sit in many lists. Deleting a watchlist removes its items
(a list is a user curation, not research data).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchlist",
        sa.Column("watchlist_id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="market",
    )
    op.create_index("ix_watchlist_name", "watchlist", ["name"], schema="market")

    op.create_table(
        "watchlist_item",
        sa.Column("watchlist_id", UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["watchlist_id"], ["market.watchlist.watchlist_id"],
            name="fk_watchlist_item_watchlist", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["market.instrument.instrument_id"],
            name="fk_watchlist_item_instrument", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("watchlist_id", "instrument_id", name="pk_watchlist_item"),
        schema="market",
    )
    op.create_index(
        "ix_watchlist_item_instrument", "watchlist_item", ["instrument_id"], schema="market"
    )


def downgrade() -> None:
    op.drop_table("watchlist_item", schema="market")
    op.drop_table("watchlist", schema="market")
