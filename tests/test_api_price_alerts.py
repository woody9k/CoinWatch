"""Create and list market-cap alerts. These tests do not send Signal."""

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.authz import permissions_for
from coinwatch.db.models import AuditEvent, Coin, PriceAlert, User
from coinwatch.db.session import create_engine
from coinwatch.services.identity import create_session

ADMIN_USER = "ada"
ADMIN_PASSWORD = "fixture-password"
CSRF = {"X-CoinWatch-Request": "1"}
BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
THRESHOLD = "10000"
SECRET_MARKERS = ("password", "secret", "seed", "private", "api_key")
PUBLIC_FIELDS = {
    "id",
    "chain",
    "coin_address",
    "condition",
    "threshold",
    "enabled",
    "created_by",
}


def test_price_alert_create_requires_a_session(api_client: TestClient) -> None:
    """POST /api/price-alerts without a session cookie is 401."""
    response = api_client.post(
        "/api/price-alerts",
        json=_body(),
        headers=CSRF,
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_viewer_price_alert_denial_is_committed(api_client: TestClient) -> None:
    """A viewer lacks alerts.manage, so create is 403 and the denial is committed.

    Viewer still has alerts.read. The denial is written before any coin lookup.
    """
    assert "alerts.read" in permissions_for("viewer")
    assert "alerts.manage" not in permissions_for("viewer")
    _seed_coin()
    _use_session(api_client, _session_for_role("viewer", "vera"))
    response = api_client.post("/api/price-alerts", json=_body(), headers=CSRF)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_authorized"
    with _session() as db:
        denied = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.denied")).all()
        assert len(denied) == 1
        assert denied[0].result == "denied"
        assert denied[0].detail == "alerts.manage"
        assert db.scalars(select(PriceAlert)).all() == []


def test_admin_saves_a_market_cap_alert(api_client: TestClient) -> None:
    """An admin saves mcap_usd_above 10000 and the list returns that row.

    ``threshold`` is the string form of ``Decimal("10000")``. The row is
    enabled and owned by the admin. Nothing is sent.
    """
    _seed_coin()
    _login(api_client)
    created = api_client.post("/api/price-alerts", json=_body(), headers=CSRF)
    assert created.status_code == 200
    body = created.json()
    assert set(body) == PUBLIC_FIELDS
    assert body["chain"] == "solana"
    assert body["coin_address"] == BOBCOIN
    assert body["condition"] == "mcap_usd_above"
    assert body["threshold"] == format(Decimal(THRESHOLD), "f")
    assert body["enabled"] is True
    with _session() as db:
        admin = db.scalar(select(User).where(User.username == ADMIN_USER))
        assert admin is not None
        assert body["created_by"] == admin.id
        row = db.get(PriceAlert, body["id"])
        assert row is not None
        assert row.threshold == Decimal(THRESHOLD)
        assert isinstance(row.threshold, Decimal)
        assert row.enabled is True
        assert row.created_by == admin.id
        created_audit = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "price_alert.create")
        ).one()
        assert created_audit.result == "ok"
        assert created_audit.after_json == {
            "id": body["id"],
            "condition": "mcap_usd_above",
            "threshold": format(Decimal(THRESHOLD), "f"),
        }
    listed = api_client.get("/api/price-alerts")
    assert listed.status_code == 200
    assert listed.json() == [body]


def test_unknown_coin_is_not_found(api_client: TestClient) -> None:
    """An unknown coin is 404 after the permission check and inserts nothing."""
    _login(api_client)
    response = api_client.post(
        "/api/price-alerts",
        json=_body(coin_address="missing-coin"),
        headers=CSRF,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    with _session() as db:
        assert db.scalars(select(PriceAlert)).all() == []
        created = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "price_alert.create")
        ).all()
        assert created == []


def test_eval_condition_inserts_nothing(api_client: TestClient) -> None:
    """Condition eval is 422 invalid_request and does not insert an alert."""
    _seed_coin()
    _login(api_client)
    response = api_client.post(
        "/api/price-alerts",
        json=_body(condition="eval"),
        headers=CSRF,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    with _session() as db:
        assert db.scalars(select(PriceAlert)).all() == []
        created = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "price_alert.create")
        ).all()
        assert created == []


def test_price_alert_rows_have_no_secret_field(api_client: TestClient) -> None:
    """A saved alert row and its audit snapshot do not contain a secret field."""
    _seed_coin()
    _login(api_client)
    created = api_client.post("/api/price-alerts", json=_body(), headers=CSRF)
    assert created.status_code == 200
    _assert_no_secret_names(created.json().keys())
    with _session() as db:
        rows = db.scalars(select(PriceAlert)).all()
        assert len(rows) == 1
        for row in rows:
            _assert_no_secret_names(row.__table__.columns.keys())
        audits = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "price_alert.create")
        ).all()
        assert len(audits) == 1
        for event in audits:
            _assert_no_secret_names(event.after_json.keys())
            _assert_no_secret_names(event.before_json.keys())


def _body(
    *,
    coin_address: str = BOBCOIN,
    condition: str = "mcap_usd_above",
    threshold: str = THRESHOLD,
) -> dict[str, str]:
    """Return a create body. ``threshold`` stays a decimal string."""
    return {
        "chain": "solana",
        "coin_address": coin_address,
        "condition": condition,
        "threshold": threshold,
    }


def _assert_no_secret_names(names: Iterable[object]) -> None:
    """Fail when a field name includes a secret marker."""
    blob = " ".join(str(name) for name in names).lower()
    for marker in SECRET_MARKERS:
        assert marker not in blob


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


def _seed_coin() -> None:
    """Insert BobCoin so a price alert can reference it."""
    with _session() as db:
        db.add(
            Coin(
                chain="solana",
                address=BOBCOIN,
                name="BobCoin",
                symbol="BOB",
                created_at=datetime(2026, 10, 5, 21, 0, tzinfo=UTC),
                status="active",
            )
        )
        db.commit()


def _session() -> Session:
    """Open a short-lived session on the test database."""
    return Session(create_engine())
