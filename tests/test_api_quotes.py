"""POST /api/quotes previews a curve quote and does not insert a trade."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coinwatch.db.models import AuditEvent, Coin, Tick, Trade, User
from coinwatch.db.session import create_engine
from coinwatch.quoting import quote_buy
from coinwatch.services.identity import create_session
from coinwatch.settings import get_settings

CSRF = {"X-CoinWatch-Request": "1"}
BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
VIRTUAL_SOL = Decimal(30)
VIRTUAL_TOKEN = Decimal(1_000_000)


def test_quote_requires_a_session(api_client: TestClient) -> None:
    """POST /api/quotes without a session cookie is 401."""
    response = api_client.post(
        "/api/quotes",
        json={"side": "buy", "coin_address": BOBCOIN, "amount": "1"},
        headers=CSRF,
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_viewer_buy_preview_matches_quote_buy(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A viewer preview of 1 SOL matches quote_buy and inserts no trade."""
    monkeypatch.setenv("COINWATCH_KILL_SWITCH", "false")
    monkeypatch.setenv("COINWATCH_PAPER_BALANCE_SOL", "10")
    observed_at = _seed_coin(status="active", graduated_at=None)
    _use_session(api_client, _session_for_role("viewer", "vera"))
    response = api_client.post(
        "/api/quotes",
        json={"side": "buy", "coin_address": BOBCOIN, "amount": "1"},
        headers=CSRF,
    )
    assert response.status_code == 200
    body = response.json()
    settings = get_settings()
    expected = quote_buy(
        Decimal(1),
        virtual_sol=VIRTUAL_SOL,
        virtual_token=VIRTUAL_TOKEN,
        observed_at=observed_at,
        now=datetime.now(UTC),
        complete=False,
        wallet_balance_sol=settings.paper_balance_sol,
        kill_switch=settings.coinwatch_kill_switch,
    )
    assert body["side"] == "buy"
    assert Decimal(body["fee_native"]) == Decimal("0.01")
    assert Decimal(body["expected_out"]) == Decimal("31945.788964181994191674")
    assert Decimal(body["price_impact_pct"]) == Decimal("4.343434343434343434")
    for field in (
        "amount_in",
        "expected_out",
        "minimum_out",
        "fee_native",
        "price_impact_pct",
        "sol_debited",
        "sol_credited",
    ):
        assert isinstance(body[field], str)
        assert Decimal(body[field]) == getattr(expected, field)
        assert body[field] == format(getattr(expected, field), "f")
    _assert_no_trade_or_preview_audit()


def test_unknown_coin_is_not_found_after_auth(api_client: TestClient) -> None:
    """An authenticated preview of a missing coin is 404."""
    _use_session(api_client, _session_for_role("viewer", "vera"))
    response = api_client.post(
        "/api/quotes",
        json={"side": "buy", "coin_address": "missing-coin", "amount": "1"},
        headers=CSRF,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_graduated_coin_is_venue_migrated(api_client: TestClient) -> None:
    """A graduated coin is 422 venue_migrated and the body is not a quote."""
    _seed_coin(status="graduated", graduated_at=datetime.now(UTC))
    _use_session(api_client, _session_for_role("viewer", "vera"))
    response = api_client.post(
        "/api/quotes",
        json={"side": "buy", "coin_address": BOBCOIN, "amount": "1"},
        headers=CSRF,
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "venue_migrated"
    assert "expected_out" not in payload
    _assert_no_trade_or_preview_audit()


def test_sell_without_position_size_is_invalid_amount(api_client: TestClient) -> None:
    """A sell that omits position_size is 422 invalid_amount."""
    _seed_coin(status="active", graduated_at=None)
    _use_session(api_client, _session_for_role("viewer", "vera"))
    response = api_client.post(
        "/api/quotes",
        json={"side": "sell", "coin_address": BOBCOIN, "amount": "1"},
        headers=CSRF,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_amount"
    _assert_no_trade_or_preview_audit()


def _assert_no_trade_or_preview_audit() -> None:
    """A preview writes no trade and no audit row."""
    with _session() as db:
        trades = db.scalar(select(func.count()).select_from(Trade))
        audits = db.scalar(select(func.count()).select_from(AuditEvent))
    assert trades == 0
    assert audits == 0


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


def _seed_coin(*, status: str, graduated_at: datetime | None) -> datetime:
    """Insert one coin and a fresh tick with the hand-check reserves."""
    observed_at = datetime.now(UTC)
    with _session() as db:
        db.add(
            Coin(
                chain="solana",
                address=BOBCOIN,
                name="BobCoin",
                symbol="BOB",
                created_at=observed_at,
                graduated_at=graduated_at,
                status=status,
            )
        )
        db.add(
            Tick(
                chain="solana",
                coin_address=BOBCOIN,
                ts=observed_at,
                price_native=Decimal("0.00003"),
                price_usd=Decimal("0.004"),
                mcap_usd=Decimal(1000),
                liquidity_native=VIRTUAL_SOL,
                curve_pct=Decimal(10),
                volume_1m=Decimal(0),
                volume_5m=Decimal(0),
                volume_15m=Decimal(0),
                holders=None,
                virtual_sol_reserves=VIRTUAL_SOL,
                virtual_token_reserves=VIRTUAL_TOKEN,
            )
        )
        db.commit()
    return observed_at


def _session() -> Session:
    """Open a short-lived session on the test database."""
    return Session(create_engine())
