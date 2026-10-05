"""SQLite engine and session factory.

Each SQLite connection enables WAL so the API and engine can share the file,
and turns foreign key checks on.
"""

import sqlite3

from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import Pool

from coinwatch.settings import get_settings


def create_engine(url: str | None = None, *, poolclass: type[Pool] | None = None) -> Engine:
    """Create a database engine for ``url`` or ``DATABASE_URL``.

    SQLite engines run ``PRAGMA journal_mode=WAL`` and ``PRAGMA foreign_keys=ON``
    on every new connection.
    """
    resolved = get_settings().database_url if url is None else url
    if poolclass is None:
        engine = sa_create_engine(resolved)
    else:
        engine = sa_create_engine(resolved, poolclass=poolclass)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_pragmas)
    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine``."""
    return sessionmaker(bind=engine)


def _enable_sqlite_pragmas(
    dbapi_connection: sqlite3.Connection,
    connection_record: object,
) -> None:
    """Enable WAL journaling and foreign key enforcement."""
    del connection_record
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.fetchone()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
