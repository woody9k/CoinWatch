"""Fixtures for API tests against a temporary SQLite database."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from coinwatch.api.__main__ import create_app

ADMIN_USER = "ada"
ADMIN_PASSWORD = "fixture-password"


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Upgrade an empty database, seed the admin, and yield an API client."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("COINWATCH_ADMIN_USER", ADMIN_USER)
    monkeypatch.setenv("COINWATCH_ADMIN_PASSWORD", ADMIN_PASSWORD)
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    with TestClient(create_app()) as client:
        yield client
