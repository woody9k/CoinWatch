"""add bots armed rules

Revision ID: d1f6a8c04e21
Revises: c4e8a91b2d10
Create Date: 2026-10-05 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1f6a8c04e21"
down_revision: str | Sequence[str] | None = "c4e8a91b2d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remember which strategy rules are already true for a bot."""
    with op.batch_alter_table("bots") as batch_op:
        batch_op.add_column(sa.Column("armed_rules", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the armed-rule memory from bots."""
    with op.batch_alter_table("bots") as batch_op:
        batch_op.drop_column("armed_rules")
