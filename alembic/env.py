"""Alembic migration environment.

``DATABASE_URL`` comes from CoinWatch settings. Schema changes are applied
only through revisions. Application startup does not call ``create_all``.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy.pool import NullPool

from coinwatch.db import models
from coinwatch.db.session import create_engine
from coinwatch.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = models.Base.metadata


def _database_url() -> str:
    """Return the database URL from process settings."""
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Emit migration SQL without opening a connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against the configured database."""
    connectable = create_engine(_database_url(), poolclass=NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
