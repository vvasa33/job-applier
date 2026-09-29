"""AI usage ledger and result cache.

Revision ID: 0005_ai_costs
Revises: 0004_agent_loop
Create Date: 2026-09-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_ai_costs"
down_revision: Union[str, Sequence[str], None] = "0004_agent_loop"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("essential", sa.Boolean(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("saved_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "resume_version_id",
            sa.Integer(),
            sa.ForeignKey("resume_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("detail", sa.String(500), nullable=True),
    )
    op.create_index("ix_ai_usage_day", "ai_usage", ["day"])
    op.create_index("ix_ai_usage_job", "ai_usage", ["job_id"])
    op.create_index("ix_ai_usage_application", "ai_usage", ["application_id"])
    op.create_table(
        "ai_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("prompt_version", sa.String(20), nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_cache")
    op.drop_index("ix_ai_usage_application", table_name="ai_usage")
    op.drop_index("ix_ai_usage_job", table_name="ai_usage")
    op.drop_index("ix_ai_usage_day", table_name="ai_usage")
    op.drop_table("ai_usage")
