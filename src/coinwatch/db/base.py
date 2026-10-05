"""SQLAlchemy declarative base and shared column types."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, Numeric
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

MONEY = Numeric(38, 18)


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
