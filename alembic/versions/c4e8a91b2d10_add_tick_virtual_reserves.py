"""add tick virtual reserves

Revision ID: c4e8a91b2d10
Revises: ec13b0f952b0
Create Date: 2026-10-05 17:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8a91b2d10"
down_revision: str | Sequence[str] | None = "ec13b0f952b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store human virtual reserves on each tick so a quote can run offline."""
    with op.batch_alter_table("ticks") as batch_op:
        batch_op.add_column(
            sa.Column("virtual_sol_reserves", sa.Numeric(precision=38, scale=18), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "virtual_token_reserves",
                sa.Numeric(precision=38, scale=18),
                nullable=True,
            )
        )


def downgrade() -> None:
    """Remove virtual reserves from ticks."""
    with op.batch_alter_table("ticks") as batch_op:
        batch_op.drop_column("virtual_token_reserves")
        batch_op.drop_column("virtual_sol_reserves")
