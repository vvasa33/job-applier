"""Store a parsed master-resume structure.

Revision ID: 0002_resume_structure
Revises: 0001_core_domain
Create Date: 2026-09-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_resume_structure"
down_revision: Union[str, Sequence[str], None] = "0001_core_domain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("resumes", sa.Column("structure", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("resumes", "structure")
