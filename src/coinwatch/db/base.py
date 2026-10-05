"""SQLAlchemy declarative base and shared column types."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, MetaData, String
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Money(TypeDecorator[Decimal]):
    """Finite ``Decimal`` stored as canonical text.

    SQLite ``NUMERIC`` affinity stores a number as ``REAL`` and coerces a
    numeric-looking string back into ``REAL``, so ``0.2`` reloads as a binary
    float. ``TEXT`` keeps the fixed-point form from ``format(value, "f")``.
    ``String(80)`` holds a 38-digit value plus a sign and a decimal point.
    """

    impl = String(80)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        """Store a finite decimal as a fixed-point string."""
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError("money value must be Decimal")
        if not value.is_finite():
            raise ValueError("money value must be finite")
        return format(value, "f")

    def process_result_value(self, value: object | None, dialect: Dialect) -> Decimal | None:
        """Load the stored value with ``Decimal(str(value))``."""
        if value is None:
            return None
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise ValueError("money value must be finite")
        return parsed


MONEY = Money


class UtcDateTime(TypeDecorator[datetime]):
    """UTC timestamp stored as a datetime and loaded with timezone UTC.

    SQLite keeps the clock time and drops the offset. Values are written in
    UTC and read back as timezone-aware UTC datetimes.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Require a timezone and store the instant in UTC."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware UTC")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Attach UTC when the stored clock time has no offset."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    """Declarative base for CoinWatch tables."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
