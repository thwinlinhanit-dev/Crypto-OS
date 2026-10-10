"""M10: perp derivatives — funding, open interest, liquidations (spec
section 10, 4.5/7.7).

Perp identity exists from M0 (R2/4.8); these tables land with the perp
adapter at M10. Spot↔perp substitution is fail-closed (PD-10): every row
carries its instrument_id, whose market_type is the authority.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "funding_rate",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column(
            "instrument_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("market.instrument.instrument_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("rate", sa.Numeric(), nullable=False),
        schema="market",
    )
    op.create_index(
        "ix_market_funding_rate_instrument_ts",
        "funding_rate",
        ["instrument_id", "ts"],
        schema="market",
        unique=True,
    )

    op.create_table(
        "open_interest",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column(
            "instrument_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("market.instrument.instrument_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("bar_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("oi", sa.Numeric(), nullable=False),
        schema="market",
    )
    op.create_index(
        "ix_market_open_interest_instrument_tf_time",
        "open_interest",
        ["instrument_id", "timeframe", "bar_time"],
        schema="market",
        unique=True,
    )

    op.create_table(
        "liquidation",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column(
            "instrument_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("market.instrument.instrument_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("qty", sa.Numeric(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        schema="market",
    )
    op.create_index(
        "ix_market_liquidation_instrument_ts",
        "liquidation",
        ["instrument_id", "ts"],
        schema="market",
    )
    op.execute(
        "ALTER TABLE market.liquidation ADD CONSTRAINT market_liquidation_side_check "
        "CHECK (side IN ('LONG','SHORT'))"
    )
    op.execute(
        "ALTER TABLE market.open_interest ADD CONSTRAINT market_open_interest_tf_check "
        "CHECK (timeframe IN ('1D','4H','1H'))"
    )


def downgrade() -> None:
    op.drop_table("liquidation", schema="market")
    op.drop_table("open_interest", schema="market")
    op.drop_table("funding_rate", schema="market")
