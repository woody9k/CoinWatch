"""GET /api/alerts lists recorded alerts and does not send them."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coinwatch.authz import permissions_for
from coinwatch.db.models import Alert, AuditEvent, Coin, User
from coinwatch.db.session import create_engine
from coinwatch.services.identity import create_session

ADMIN_USER = "ada"
ADMIN_PASSWORD = "fixture-password"
CSRF = {"X-CoinWatch-Request": "1"}
BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
MESSAGE = "mcap_usd_above 10000 11000"
SENT_AT = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)
_ALERT_FIELDS = {"id", "chain", "coin_address", "type", "message", "sent_at", "escalated"}


def test_alerts_require_a_session(api_client: TestClient) -> None:
    """A read without a session cookie is 401 and inserts no alert."""
    before = _alert_count()
    response = api_client.get("/api/alerts")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert _alert_count() == before


def test_role_without_alerts_read_is_denied_and_audited(api_client: TestClient) -> None:
    """A role that lacks alerts.read is 403, and the denial is committed.

    ``permissions_for("viewer")`` includes ``alerts.read`` because that
    permission ends in ``.read`` and is not in the viewer exclusion. This
    caller uses a role that grants nothing, so the check fails before any
    alert lookup.
    """
    assert "alerts.read" in permissions_for("viewer")
    assert "alerts.read" not in permissions_for("custom")
    before = _alert_count()
    _use_session(api_client, _session_for_role("custom", "narrow"))
    response = api_client.get("/api/alerts")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_authorized"
    with _session() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.denied")).all()
        assert len(rows) == 1
        assert rows[0].detail == "alerts.read"
        assert rows[0].result == "denied"
    assert _alert_count() == before


def test_viewer_lists_a_seeded_unsent_alert(api_client: TestClient) -> None:
    """A viewer sees the stored message, a null sent_at, and newest id first."""
    unsent_id, sent_id = _seed_alerts()
    _use_session(api_client, _session_for_role("viewer", "vera"))
    response = api_client.get("/api/alerts")
    assert response.status_code == 200
    listed = response.json()
    assert [row["id"] for row in listed] == [unsent_id, sent_id]
    unsent = listed[0]
    assert set(unsent) == _ALERT_FIELDS
    assert unsent["chain"] == "solana"
    assert unsent["coin_address"] == BOBCOIN
    assert unsent["type"] == "price_alert"
    assert unsent["message"] == MESSAGE
    assert unsent["sent_at"] is None
    assert unsent["escalated"] is False
    assert listed[1]["sent_at"] == SENT_AT.isoformat()
    assert listed[1]["escalated"] is False


def test_alert_limit_above_the_cap_is_rejected(api_client: TestClient) -> None:
    """A limit above 200 is 422 invalid_limit and leaves every row unchanged."""
    _seed_alerts()
    before = _alert_snapshots()
    _login(api_client)
    response = api_client.get("/api/alerts", params={"limit": 201})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_limit"
    assert _alert_snapshots() == before


def test_list_does_not_change_sent_at(api_client: TestClient) -> None:
    """Reading the list leaves sent_at as stored, including a null sent_at."""
    _seed_alerts()
    before = _alert_snapshots()
    _login(api_client)
    response = api_client.get("/api/alerts")
    assert response.status_code == 200
    assert _alert_snapshots() == before


def test_empty_alert_list(api_client: TestClient) -> None:
    """An authorized caller with no rows gets an empty list."""
    _login(api_client)
    response = api_client.get("/api/alerts")
    assert response.status_code == 200
    assert response.json() == []
    assert _alert_count() == 0


def _login(client: TestClient) -> None:
    """Sign in as the bootstrap admin. The session cookie stays on ``client``."""
    response = client.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=CSRF,
    )
    assert response.status_code == 200


def _use_session(client: TestClient, session_id: str) -> None:
    """Attach a server session cookie without logging in through the password form."""
    client.cookies.set("coinwatch_session", session_id)


def _session_for_role(role: str, username: str) -> str:
    """Insert a user with ``role`` and return a live session id."""
    with _session() as db:
        user = User(
            username=username,
            password_hash="not-a-password-hash",
            role=role,
            created_at=datetime.now(UTC),
        )
        db.add(user)
        db.commit()
        token = create_session(db, user)
        db.commit()
        return token


def _seed_alerts() -> tuple[int, int]:
    """Insert a sent row, then an unsent crossing, and return those ids.

    The sent row is inserted first so it has the lower id and a non-null
    ``sent_at``. Newest-first order is by id, so the unsent row comes first.
    """
    with _session() as db:
        db.add(
            Coin(
                chain="solana",
                address=BOBCOIN,
                name="BobCoin",
                symbol="BOB",
                created_at=SENT_AT,
                status="active",
            )
        )
        db.flush()
        sent = Alert(
            chain="solana",
            coin_address=BOBCOIN,
            type="test",
            message="CoinWatch test alert",
            sent_at=SENT_AT,
            escalated=False,
        )
        unsent = Alert(
            chain="solana",
            coin_address=BOBCOIN,
            type="price_alert",
            message=MESSAGE,
            sent_at=None,
            escalated=False,
        )
        db.add(sent)
        db.add(unsent)
        db.commit()
        assert sent.id > 0
        assert unsent.id > sent.id
        return unsent.id, sent.id


def _alert_snapshots() -> list[tuple[int, datetime | None, bool, str]]:
    """Return id, sent_at, escalated, and message for every alert row."""
    with _session() as db:
        rows = db.scalars(select(Alert).order_by(Alert.id)).all()
        return [(row.id, row.sent_at, row.escalated, row.message) for row in rows]


def _alert_count() -> int:
    """Return how many alert rows exist."""
    with _session() as db:
        count = db.scalar(select(func.count()).select_from(Alert))
        assert count is not None
        return count


def _session() -> Session:
    """Open a short-lived session on the test database."""
    return Session(create_engine())
