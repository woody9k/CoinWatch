"""Read routes: coins, ticks, audit, and price alerts."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.db.models import AuditEvent, Coin, PriceAlert, Tick, User
from coinwatch.db.session import create_engine
from coinwatch.services.identity import create_session

ADMIN_USER = "ada"
ADMIN_PASSWORD = "fixture-password"
CSRF = {"X-CoinWatch-Request": "1"}
BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
QUIET = "quietcoin"
EARLIER = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 5, 21, 1, tzinfo=UTC)


def test_admin_lists_seeded_coin_and_latest_tick(api_client: TestClient) -> None:
    """An admin sees the seeded coin, its newest tick, and decimal strings."""
    _seed_market()
    _login(api_client)
    response = api_client.get("/api/coins")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    by_address = {item["address"]: item for item in body}
    quiet = by_address[QUIET]
    assert quiet["tick"] is None
    assert quiet["graduated_at"] is None
    coin = by_address[BOBCOIN]
    assert coin["chain"] == "solana"
    assert coin["name"] == "BobCoin"
    assert coin["symbol"] == "BOB"
    assert coin["status"] == "active"
    assert coin["created_at"] == EARLIER.isoformat()
    assert coin["graduated_at"] is None
    tick = coin["tick"]
    assert tick["ts"] == LATER.isoformat()
    assert tick["curve_pct"] is None
    assert tick["virtual_sol_reserves"] is None
    assert tick["virtual_token_reserves"] is None
    assert "volume_1m" not in tick
    for field in ("price_native", "price_usd", "mcap_usd", "liquidity_native"):
        assert isinstance(tick[field], str)
    assert Decimal(tick["price_native"]) == Decimal("1.50")
    assert Decimal(tick["price_usd"]) == Decimal("3.00")

    alerts = api_client.get("/api/price-alerts")
    assert alerts.status_code == 200
    listed = alerts.json()
    assert len(listed) == 1
    assert listed[0]["chain"] == "solana"
    assert listed[0]["coin_address"] == BOBCOIN
    assert listed[0]["condition"] == "above"
    assert isinstance(listed[0]["threshold"], str)
    assert Decimal(listed[0]["threshold"]) == Decimal("2.50")
    assert listed[0]["enabled"] is True


def test_viewer_can_read_coins_and_ticks(api_client: TestClient) -> None:
    """A viewer session can list coins and ticks for a seeded coin."""
    _seed_market()
    _use_session(api_client, _session_for_role("viewer", "vera"))
    coins = api_client.get("/api/coins")
    assert coins.status_code == 200
    assert any(item["address"] == BOBCOIN for item in coins.json())
    ticks = api_client.get("/api/ticks", params={"chain": "solana", "address": BOBCOIN})
    assert ticks.status_code == 200
    rows = ticks.json()
    assert [row["ts"] for row in rows] == [LATER.isoformat(), EARLIER.isoformat()]
    assert isinstance(rows[0]["volume_1m"], str)
    assert rows[0]["holders"] is None
    assert rows[1]["holders"] == 42
    assert isinstance(rows[1]["volume_5m"], str)
    assert isinstance(rows[1]["volume_15m"], str)


def test_viewer_audit_denial_is_committed_and_visible_to_admin(api_client: TestClient) -> None:
    """A viewer audit read is 403, and the admin audit list includes that denial."""
    _use_session(api_client, _session_for_role("viewer", "vera"))
    denied = api_client.get("/api/audit")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "not_authorized"
    with _session() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.denied")).all()
        assert len(rows) == 1
        assert rows[0].result == "denied"
        assert rows[0].detail == "audit.read"
    _login(api_client)
    audit = api_client.get("/api/audit")
    assert audit.status_code == 200
    actions = [item["action"] for item in audit.json()]
    assert "auth.denied" in actions
    denial = next(item for item in audit.json() if item["action"] == "auth.denied")
    assert denial["result"] == "denied"
    assert set(denial) == {
        "id",
        "ts",
        "actor_type",
        "actor_id",
        "action",
        "entity_type",
        "entity_id",
        "result",
        "before_json",
        "after_json",
        "request_id",
        "detail",
    }
    assert isinstance(denial["ts"], str)


def test_missing_coin_ticks_are_not_found_for_admin(api_client: TestClient) -> None:
    """An admin asking for ticks of an unknown coin gets 404."""
    _login(api_client)
    response = api_client.get(
        "/api/ticks",
        params={"chain": "solana", "address": "missing-coin"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_ticks_permission_is_checked_before_not_found(api_client: TestClient) -> None:
    """A caller without ticks.read is 403 even when the coin does not exist."""
    _use_session(api_client, _session_for_role("custom", "narrow"))
    response = api_client.get(
        "/api/ticks",
        params={"chain": "solana", "address": "missing-coin"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_authorized"
    with _session() as db:
        denied = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.denied")).all()
        assert len(denied) == 1
        assert denied[0].detail == "ticks.read"


def test_coins_require_a_session(api_client: TestClient) -> None:
    """GET /api/coins without a session cookie is 401."""
    response = api_client.get("/api/coins")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_tick_limit_above_the_cap_is_clamped(api_client: TestClient) -> None:
    """A limit above 500 is accepted and capped. limit=1 returns one row."""
    _seed_market()
    _login(api_client)
    params = {"chain": "solana", "address": BOBCOIN}
    capped = api_client.get("/api/ticks", params={**params, "limit": 500})
    assert capped.status_code == 200
    assert len(capped.json()) == 2
    above = api_client.get("/api/ticks", params={**params, "limit": 501})
    assert above.status_code == 200
    assert len(above.json()) == 2
    single = api_client.get("/api/ticks", params={**params, "limit": 1})
    assert single.status_code == 200
    assert len(single.json()) == 1
    assert single.json()[0]["ts"] == LATER.isoformat()


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


def _seed_market() -> None:
    """Insert BobCoin, two ticks, a coin with no tick, and one price alert."""
    with _session() as db:
        db.add(
            Coin(
                chain="solana",
                address=BOBCOIN,
                name="BobCoin",
                symbol="BOB",
                created_at=EARLIER,
                status="active",
            )
        )
        db.add(
            Coin(
                chain="solana",
                address=QUIET,
                name="Quiet",
                symbol="QUIET",
                created_at=EARLIER,
                status="active",
            )
        )
        db.add(
            Tick(
                chain="solana",
                coin_address=BOBCOIN,
                ts=EARLIER,
                price_native=Decimal("1.25"),
                price_usd=Decimal("2.00"),
                mcap_usd=Decimal("10000.00"),
                liquidity_native=Decimal("12.50"),
                curve_pct=Decimal("40.5"),
                volume_1m=Decimal("1.00"),
                volume_5m=Decimal("2.00"),
                volume_15m=Decimal("3.00"),
                holders=42,
                virtual_sol_reserves=Decimal(30),
                virtual_token_reserves=Decimal(1_000_000),
            )
        )
        db.add(
            Tick(
                chain="solana",
                coin_address=BOBCOIN,
                ts=LATER,
                price_native=Decimal("1.50"),
                price_usd=Decimal("3.00"),
                mcap_usd=Decimal("11000.00"),
                liquidity_native=Decimal("13.00"),
                curve_pct=None,
                volume_1m=Decimal("1.10"),
                volume_5m=Decimal("2.10"),
                volume_15m=Decimal("3.10"),
                holders=None,
                virtual_sol_reserves=None,
                virtual_token_reserves=None,
            )
        )
        admin = db.scalar(select(User).where(User.username == ADMIN_USER))
        assert admin is not None
        db.add(
            PriceAlert(
                chain="solana",
                coin_address=BOBCOIN,
                condition="above",
                threshold=Decimal("2.50"),
                enabled=True,
                created_by=admin.id,
            )
        )
        db.commit()


def _session() -> Session:
    """Open a short-lived session on the test database."""
    return Session(create_engine())
