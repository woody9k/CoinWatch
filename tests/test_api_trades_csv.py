"""GET /api/trades.csv downloads recent fills and does not insert one."""

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coinwatch.authz import permissions_for
from coinwatch.db.models import AuditEvent, Bot, Coin, Strategy, Trade, User, Wallet
from coinwatch.db.session import create_engine
from coinwatch.services.identity import create_session

ADMIN_USER = "ada"
ADMIN_PASSWORD = "fixture-password"
CSRF = {"X-CoinWatch-Request": "1"}
BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
FILLED_AT = datetime(2026, 10, 5, 21, 1, tzinfo=UTC)


def test_trades_csv_requires_a_session(api_client: TestClient) -> None:
    """A download without a session cookie is 401 and the body is JSON."""
    response = api_client.get("/api/trades.csv")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "unauthenticated"


def test_role_without_trades_read_is_denied_and_audited(api_client: TestClient) -> None:
    """A role that grants nothing is 403, and the denial audit is committed.

    ``permissions_for("viewer")`` includes ``trades.read``. This caller uses
    ``custom``, which grants nothing, so the check fails before the query.
    """
    assert "trades.read" not in permissions_for("custom")
    assert "trades.read" in permissions_for("viewer")
    _use_session(api_client, _session_for_role("custom", "narrow"))
    response = api_client.get("/api/trades.csv")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_authorized"
    with _session() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.denied")).all()
        assert len(rows) == 1
        assert rows[0].detail == "trades.read"
        assert rows[0].result == "denied"


def test_viewer_downloads_a_seeded_paper_trade(api_client: TestClient) -> None:
    """A viewer gets the header and one paper row, and the trade count stays put."""
    _seed_one_paper_trade()
    before = _trade_count()
    _use_session(api_client, _session_for_role("viewer", "vera"))
    response = api_client.get("/api/trades.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="trades.csv"'
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == [
        "id",
        "ts",
        "side",
        "chain",
        "coin_address",
        "amount_native",
        "fee_native",
        "price_impact_pct",
        "paper",
    ]
    assert len(rows) == 2
    downloaded = rows[1]
    assert downloaded[1] == FILLED_AT.isoformat()
    assert downloaded[2] == "buy"
    assert downloaded[3] == "solana"
    assert downloaded[4] == BOBCOIN
    assert downloaded[5] == format(Decimal("0.50"), "f")
    assert downloaded[6] == format(Decimal("0.125"), "f")
    assert downloaded[7] == format(Decimal("1.5"), "f")
    assert downloaded[8] == "true"
    assert "tx_sig" not in response.text
    assert "actor_id" not in response.text
    assert _trade_count() == before


def test_trade_csv_limit_above_the_cap_is_rejected(api_client: TestClient) -> None:
    """A limit above 200 is 422 invalid_limit and inserts no trade."""
    before = _trade_count()
    _login(api_client)
    response = api_client.get("/api/trades.csv", params={"limit": 201})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_limit"
    assert _trade_count() == before


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


def _seed_one_paper_trade() -> None:
    """Insert one paper fill so the CSV has a single data row."""
    with _session() as db:
        admin = db.scalar(select(User).where(User.username == ADMIN_USER))
        assert admin is not None
        db.add(
            Coin(
                chain="solana",
                address=BOBCOIN,
                name="BobCoin",
                symbol="BOB",
                created_at=FILLED_AT,
                status="active",
            )
        )
        db.flush()
        wallet = Wallet(
            chain="solana",
            label="paper",
            public_address="FakeWallet111111111111111111111111111111111",
            created_by=admin.id,
            created_at=FILLED_AT,
        )
        strategy = Strategy(
            name="paper",
            yaml_config="name: paper\n",
            created_by=admin.id,
            created_at=FILLED_AT,
            updated_at=FILLED_AT,
        )
        db.add(wallet)
        db.add(strategy)
        db.flush()
        bot = Bot(
            chain="solana",
            coin_address=BOBCOIN,
            strategy_id=strategy.id,
            wallet_id=wallet.id,
            status="running",
            paper=True,
            created_by=admin.id,
            created_at=FILLED_AT,
        )
        db.add(bot)
        db.flush()
        db.add(
            Trade(
                bot_id=bot.id,
                chain="solana",
                coin_address=BOBCOIN,
                side="buy",
                amount_native=Decimal("0.50"),
                price_native=Decimal("0.00003"),
                price_usd=Decimal("0.004"),
                mcap_usd=Decimal(11000),
                fee_native=Decimal("0.125"),
                price_impact_pct=Decimal("1.5"),
                tx_sig=None,
                paper=True,
                actor_id="system",
                ts=FILLED_AT,
            )
        )
        db.commit()


def _trade_count() -> int:
    """Return how many trade rows exist."""
    with _session() as db:
        count = db.scalar(select(func.count()).select_from(Trade))
        assert count is not None
        return count


def _session() -> Session:
    """Open a short-lived session on the test database."""
    return Session(create_engine())
