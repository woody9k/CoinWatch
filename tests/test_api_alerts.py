"""POST /api/alerts/test uses an injected sender and never starts signal-cli."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.alerts.base import AlertSender
from coinwatch.api.app import create_app
from coinwatch.db.models import Alert, AuditEvent, User
from coinwatch.db.session import create_engine
from coinwatch.errors import AlertSendError
from coinwatch.services.identity import create_session

ADMIN_USER = "ada"
ADMIN_PASSWORD = "fixture-password"
CSRF = {"X-CoinWatch-Request": "1"}
RECIPIENT = "+14075150936"
MESSAGE = "CoinWatch test alert"


class RecordingSender:
    """Alert sender that records calls and does not start a process."""

    def __init__(self) -> None:
        """Start with an empty call list."""
        self.calls: list[tuple[str, str]] = []

    def send(self, recipient: str, message: str) -> None:
        """Record one send."""
        self.calls.append((recipient, message))


class FailingSender:
    """Alert sender that fails before any transport runs."""

    def send(self, recipient: str, message: str) -> None:
        """Raise the same error a failed signal-cli run would raise."""
        del recipient, message
        raise AlertSendError(3)


@pytest.fixture
def open_alerts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[object]:
    """Upgrade a temp database and return a client factory bound to a sender."""
    database_path = tmp_path / "coinwatch.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("COINWATCH_ADMIN_USER", ADMIN_USER)
    monkeypatch.setenv("COINWATCH_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("SIGNAL_CLI_BIN", "signal-cli")
    monkeypatch.setenv("SIGNAL_ACCOUNT", RECIPIENT)
    monkeypatch.setenv("SIGNAL_RECIPIENT", RECIPIENT)
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")

    @contextmanager
    def open_client(sender: AlertSender) -> Iterator[TestClient]:
        with TestClient(create_app(alert_sender=sender)) as client:
            yield client

    yield open_client


def test_admin_test_alert_records_sent_row_and_ok_audit(
    open_alerts: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An admin send stores sent_at, an ok audit row, and a body-free log line."""
    sender = RecordingSender()
    caplog.set_level(logging.INFO)
    with _as_client(open_alerts, sender) as client:
        _login(client)
        response = client.post("/api/alerts/test", headers=CSRF)
    assert response.status_code == 200
    body = response.json()
    assert sender.calls == [(RECIPIENT, MESSAGE)]
    with _session() as db:
        alert = db.get(Alert, body["id"])
        assert alert is not None
        assert alert.type == "test"
        assert alert.message == MESSAGE
        assert alert.chain == "solana"
        assert alert.coin_address is None
        assert alert.escalated is False
        assert alert.sent_at is not None
        assert body["sent_at"] == alert.sent_at.isoformat()
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "alert.send")).all()
        assert len(events) == 1
        assert events[0].result == "ok"
        assert events[0].entity_type == "alert"
        assert events[0].entity_id == str(alert.id)
    logged = [record.getMessage() for record in caplog.records]
    payloads = [json.loads(line) for line in logged if line.startswith("{")]
    sends = [item for item in payloads if item.get("event") == "alert.send"]
    assert len(sends) == 1
    assert sends[0]["result"] == "ok"
    assert all(MESSAGE not in line for line in logged)


def test_failed_send_is_502_and_keeps_unsent_audit(open_alerts: object) -> None:
    """A sender failure is 502, leaves sent_at null, and commits an error audit."""
    with _as_client(open_alerts, FailingSender()) as client:
        _login(client)
        response = client.post("/api/alerts/test", headers=CSRF)
    assert response.status_code == 502
    assert response.json() == {"error": {"code": "alert_failed", "message": "Alert was not sent."}}
    with _session() as db:
        alerts = db.scalars(select(Alert)).all()
        assert len(alerts) == 1
        assert alerts[0].sent_at is None
        assert alerts[0].message == MESSAGE
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "alert.send")).all()
        assert len(events) == 1
        assert events[0].result == "error"


def test_viewer_test_alert_is_forbidden(open_alerts: object) -> None:
    """A viewer is 403 and the denial audit is kept."""
    with _as_client(open_alerts, RecordingSender()) as client:
        with _session() as db:
            viewer = User(
                username="viewer",
                password_hash="not-a-password-hash",
                role="viewer",
                created_at=datetime.now(UTC),
            )
            db.add(viewer)
            db.commit()
            token = create_session(db, viewer)
            db.commit()
        client.cookies.set("coinwatch_session", token)
        response = client.post("/api/alerts/test", headers=CSRF)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_authorized"
    with _session() as db:
        assert db.scalars(select(Alert)).all() == []
        denied = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.denied")).all()
        assert len(denied) == 1
        assert denied[0].result == "denied"


def test_test_alert_without_request_header_inserts_nothing(open_alerts: object) -> None:
    """A post without the request header is 403 and does not insert an alert."""
    with _as_client(open_alerts, RecordingSender()) as client:
        _login(client)
        response = client.post("/api/alerts/test")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "missing_request_header"
    with _session() as db:
        assert db.scalars(select(Alert)).all() == []


def _as_client(open_alerts: object, sender: AlertSender) -> Iterator[TestClient]:
    """Open the factory returned by the fixture."""
    opener = open_alerts
    if not callable(opener):
        raise TypeError("alert client factory is missing")
    return opener(sender)


def _login(client: TestClient) -> None:
    """Sign in as the seeded admin."""
    response = client.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=CSRF,
    )
    assert response.status_code == 200


def _session() -> Session:
    """Open a short-lived session on the test database."""
    return Session(create_engine())
