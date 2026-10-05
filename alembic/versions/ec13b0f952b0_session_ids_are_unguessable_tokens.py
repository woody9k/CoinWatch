"""session ids are unguessable tokens

Revision ID: ec13b0f952b0
Revises: 06a8ced04527
Create Date: 2026-10-05 17:16:28.332389

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ec13b0f952b0"
down_revision: str | Sequence[str] | None = "06a8ced04527"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store session ids as unguessable strings the cookie can hold."""
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.alter_column(
            "id",
            existing_type=sa.Integer(),
            type_=sa.String(length=64),
            existing_nullable=False,
            autoincrement=False,
        )


def downgrade() -> None:
    """Restore integer session ids."""
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.alter_column(
            "id",
            existing_type=sa.String(length=64),
            type_=sa.Integer(),
            existing_nullable=False,
            autoincrement=True,
        )
