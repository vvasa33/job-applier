"""Track application status changes and the resume version in use.

Revision ID: 0003_application_tracking
Revises: 0002_resume_structure
Create Date: 2026-09-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_application_tracking"
down_revision: Union[str, Sequence[str], None] = "0002_resume_structure"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("applications", sa.Column("resume_version_id", sa.Integer(), nullable=True))
    op.execute("UPDATE applications SET status_changed_at = queued_at WHERE status_changed_at IS NULL")


def downgrade() -> None:
    op.drop_column("applications", "resume_version_id")
    op.drop_column("applications", "status_changed_at")
