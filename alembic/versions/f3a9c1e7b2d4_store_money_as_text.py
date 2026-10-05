"""store money as text

Revision ID: f3a9c1e7b2d4
Revises: d1f6a8c04e21
Create Date: 2026-10-05 23:30:00.000000

"""

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from coinwatch.db.base import NAMING_CONVENTION

# revision identifiers, used by Alembic.
revision: str = "f3a9c1e7b2d4"
down_revision: str | Sequence[str] | None = "d1f6a8c04e21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table, primary key, (column, nullable)
_MONEY_TABLES: tuple[tuple[str, str, tuple[tuple[str, bool], ...]], ...] = (
    (
        "ticks",
        "id",
        (
            ("price_native", False),
            ("price_usd", False),
            ("mcap_usd", False),
            ("liquidity_native", False),
            ("curve_pct", True),
            ("volume_1m", False),
            ("volume_5m", False),
            ("volume_15m", False),
            ("virtual_sol_reserves", True),
            ("virtual_token_reserves", True),
        ),
    ),
    (
        "positions",
        "bot_id",
        (
            ("size", False),
            ("cost_native", False),
            ("realized_pnl_native", False),
        ),
    ),
    (
        "trades",
        "id",
        (
            ("amount_native", False),
            ("price_native", False),
            ("price_usd", False),
            ("mcap_usd", False),
            ("fee_native", False),
            ("price_impact_pct", False),
        ),
    ),
    ("price_alerts", "id", (("threshold", False),)),
)

_NUMERIC = sa.Numeric(precision=38, scale=18)
_TEXT = sa.String(length=80)


def upgrade() -> None:
    """Rebuild money columns as text and rewrite reals as fixed-point decimals."""
    for table_name, primary_key, columns in _MONEY_TABLES:
        saved = _snapshot_money(table_name, primary_key, columns)
        _rebuild(table_name, columns, existing_type=_NUMERIC, type_=_TEXT)
        _restore_money(table_name, primary_key, columns, saved)


def downgrade() -> None:
    """Rebuild money columns as ``NUMERIC(38, 18)``. This is not lossless."""
    for table_name, _primary_key, columns in _MONEY_TABLES:
        _rebuild(table_name, columns, existing_type=_TEXT, type_=_NUMERIC)


def _snapshot_money(
    table_name: str,
    primary_key: str,
    columns: tuple[tuple[str, bool], ...],
) -> list[dict[str, object]]:
    """Read money cells before the table rebuild changes their storage class."""
    names = [name for name, _nullable in columns]
    selected = ", ".join([primary_key, *names])
    rows = op.get_bind().execute(sa.text(f"SELECT {selected} FROM {table_name}")).mappings().all()
    return [
        {
            primary_key: row[primary_key],
            **{name: _canonical_money(row[name]) for name in names},
        }
        for row in rows
    ]


def _restore_money(
    table_name: str,
    primary_key: str,
    columns: tuple[tuple[str, bool], ...],
    saved: list[dict[str, object]],
) -> None:
    """Write snapshotted money text back after the columns have text affinity."""
    if not saved:
        return
    names = [name for name, _nullable in columns]
    assignments = ", ".join(f"{name} = :{name}" for name in names)
    op.get_bind().execute(
        sa.text(f"UPDATE {table_name} SET {assignments} WHERE {primary_key} = :{primary_key}"),
        saved,
    )


def _rebuild(
    table_name: str,
    columns: tuple[tuple[str, bool], ...],
    *,
    existing_type: sa.types.TypeEngine[object],
    type_: sa.types.TypeEngine[object],
) -> None:
    """Rebuild one table in batch mode so money columns change type."""
    with op.batch_alter_table(
        table_name,
        recreate="always",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        for name, nullable in columns:
            batch_op.alter_column(
                name,
                existing_type=existing_type,
                type_=type_,
                existing_nullable=nullable,
            )


def _canonical_money(value: object) -> str | None:
    """Keep text as text, and render a real or integer without scientific notation."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise TypeError("money value must be text or a finite number")
    decimal_value = Decimal(str(value))
    if not decimal_value.is_finite():
        raise ValueError("money value must be finite")
    return format(decimal_value, "f")
