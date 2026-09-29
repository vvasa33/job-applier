"""Agent worker state, activity log, and bounded retry counts.

Revision ID: 0004_agent_loop
Revises: 0003_application_tracking
Create Date: 2026-09-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_agent_loop"
down_revision: Union[str, Sequence[str], None] = "0003_application_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("desired", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_discovery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_discovery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_job_id", sa.Integer(), nullable=True),
        sa.Column("current_application_id", sa.Integer(), nullable=True),
        sa.Column("current_step", sa.String(300), nullable=True),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_agent_state_singleton"),
    )
    op.execute(
        """
        INSERT INTO agent_state (id, desired, phase, updated_at)
        VALUES (1, 'stopped', 'stopped', CURRENT_TIMESTAMP)
        """
    )
    op.create_table(
        "agent_activity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("message", sa.String(1000), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("application_id", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_agent_activity_created", "agent_activity", ["created_at"])
    op.create_table(
        "agent_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gave_up", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("stage", "subject_id", name="uq_agent_attempts_subject"),
    )


def downgrade() -> None:
    op.drop_table("agent_attempts")
    op.drop_index("ix_agent_activity_created", table_name="agent_activity")
    op.drop_table("agent_activity")
    op.drop_table("agent_state")
