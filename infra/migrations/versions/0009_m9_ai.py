"""M9: AI runs + tool calls (spec section 10, 9).

agents.run / agents.tool_call transcribe the spec §10 DDL verbatim; every
MCP and Copilot call writes agents.tool_call **and** system.audit_log
(D-062, the M9 AC: "MCP calls audited").
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run",
        sa.Column(
            "run_id",
            sa.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("user_prompt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="agents",
    )
    op.create_index("ix_agents_run_created_at", "run", ["created_at"], schema="agents")

    op.create_table(
        "tool_call",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column(
            "run_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("agents.run.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("args", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="agents",
    )
    op.create_index(
        "ix_agents_tool_call_run_id", "tool_call", ["run_id"], schema="agents"
    )

    op.execute(
        "ALTER TABLE agents.run ADD CONSTRAINT agents_run_mode_check "
        "CHECK (mode IN ('Research','Quant','Data','Critic','Writer'))"
    )
    op.execute(
        "ALTER TABLE agents.tool_call ADD CONSTRAINT agents_tool_call_tool_nonempty "
        "CHECK (length(tool) > 0)"
    )


def downgrade() -> None:
    op.drop_table("tool_call", schema="agents")
    op.drop_table("run", schema="agents")
