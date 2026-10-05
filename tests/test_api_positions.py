"""GET /api/positions and GET /api/trades list fills and do not insert one."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coinwatch.authz import permissions_for
from coinwatch.db.models import AuditEvent, Bot, Coin, Position, Strategy, Trade, User, Wallet
from coinwatch.db.session import create_engine
from coinwatch.services.identity import create_session

ADMIN_USER = "ada"
ADMIN_PASSWORD = "fixture-password"
CSRF = {"X-CoinWatch-Request": "1"}
BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
EARLIER = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 5, 21, 1, tzinfo=UTC)
_MONEY_FIELDS = (
    "amount_native",
    "price_native",
    "price_usd",
    "mcap_usd",
    "fee_native",
    "price_impact_pct",
)


def test_positions_and_trades_require_a_session(api_client: TestClient) -> None:
    """Both reads without a session cookie are 401 and insert no trade."""
    before = _trade_count()
    positions = api_client.get("/api/positions")
    trades = api_client.get("/api/trades")
    assert positions.status_code == 401
    assert positions.json()["error"]["code"] == "unauthenticated"
    assert trades.status_code == 401
    assert trades.json()["error"]["code"] == "unauthenticated"
    assert _trade_count() == before


def test_role_without_trades_read_is_denied_and_audited(api_client: TestClient) -> None:
    """A role that lacks trades.read is 403, and each denial is committed.

    ``permissions_for("auditor")`` includes ``trades.read`` because auditor is
    the viewer bundle plus ``audit.read``. This caller uses a role that grants
    nothing, so the check fails before any position or trade lookup.
    """
    assert "trades.read" not in permissions_for("custom")
    assert "audit.read" in permissions_for("auditor")
    assert "trades.read" in permissions_for("auditor")
    _use_session(api_client, _session_for_role("custom", "narrow"))
    positions = api_client.get("/api/positions")
    trades = api_client.get("/api/trades")
    assert positions.status_code == 403
    assert positions.json()["error"]["code"] == "not_authorized"
    assert trades.status_code == 403
    assert trades.json()["error"]["code"] == "not_authorized"
    with _session() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.denied")).all()
        assert len(rows) == 2
        assert {row.detail for row in rows} == {"trades.read"}
        assert {row.result for row in rows} == {"denied"}


def test_viewer_lists_a_seeded_position_and_trade(api_client: TestClient) -> None:
    """A viewer sees decimal strings, a paper fill, and no new trade row."""
    _seed_book()
    before = _trade_count()
    _use_session(api_client, _session_for_role("viewer", "vera"))
    positions = api_client.get("/api/positions")
    trades = api_client.get("/api/trades")
    assert positions.status_code == 200
    assert trades.status_code == 200
    listed_positions = positions.json()
    listed_trades = trades.json()
    assert len(listed_positions) == 1
    position = listed_positions[0]
    assert type(position["bot_id"]) is int
    assert position["chain"] == "solana"
    assert position["coin_address"] == BOBCOIN
    assert position["updated_at"] == LATER.isoformat()
    assert isinstance(position["size"], str)
    assert isinstance(position["cost_native"], str)
    assert isinstance(position["realized_pnl_native"], str)
    assert Decimal(position["size"]) == Decimal("1000.5")
    assert Decimal(position["cost_native"]) == Decimal("1.25")
    assert Decimal(position["realized_pnl_native"]) == Decimal("-0.25")
    assert [row["ts"] for row in listed_trades] == [LATER.isoformat(), EARLIER.isoformat()]
    newest = listed_trades[0]
    assert newest["paper"] is True
    assert newest["tx_sig"] is None
    assert newest["side"] == "buy"
    assert newest["actor_id"] == "system"
    assert isinstance(newest["actor_id"], str)
    for field in _MONEY_FIELDS:
        assert isinstance(newest[field], str)
    assert Decimal(newest["fee_native"]) == Decimal("0.125")
    assert Decimal(newest["amount_native"]) == Decimal("0.50")
    assert Decimal(newest["price_impact_pct"]) == Decimal("1.5")
    assert _trade_count() == before


def test_trade_limit_above_the_cap_is_rejected(api_client: TestClient) -> None:
    """A limit above 200 is 422 invalid_limit and inserts no trade."""
    _seed_book()
    before = _trade_count()
    _login(api_client)
    response = api_client.get("/api/trades", params={"limit": 201})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_limit"
    assert _trade_count() == before


def test_empty_position_and_trade_lists(api_client: TestClient) -> None:
    """An authorized caller with no rows gets empty lists, not 404."""
    _login(api_client)
    positions = api_client.get("/api/positions")
    trades = api_client.get("/api/trades")
    assert positions.status_code == 200
    assert positions.json() == []
    assert trades.status_code == 200
    assert trades.json() == []
    assert _trade_count() == 0


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


def _seed_book() -> None:
    """Insert one paper position and two fills, newest last so order is visible."""
    with _session() as db:
        admin = db.scalar(select(User).where(User.username == ADMIN_USER))
        assert admin is not None
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
        db.flush()
        wallet = Wallet(
            chain="solana",
            label="paper",
            public_address="FakeWallet111111111111111111111111111111111",
            created_by=admin.id,
            created_at=EARLIER,
        )
        strategy = Strategy(
            name="paper",
            yaml_config="name: paper\n",
            created_by=admin.id,
            created_at=EARLIER,
            updated_at=EARLIER,
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
            created_at=EARLIER,
        )
        db.add(bot)
        db.flush()
        db.add(
            Position(
                bot_id=bot.id,
                chain="solana",
                coin_address=BOBCOIN,
                size=Decimal("1000.5"),
                cost_native=Decimal("1.25"),
                realized_pnl_native=Decimal("-0.25"),
                updated_at=LATER,
            )
        )
        db.add(
            _fill(
                bot_id=bot.id,
                ts=EARLIER,
                side="sell",
                amount_native=Decimal("0.20"),
                fee_native=Decimal("0.25"),
            )
        )
        db.add(
            _fill(
                bot_id=bot.id,
                ts=LATER,
                side="buy",
                amount_native=Decimal("0.50"),
                fee_native=Decimal("0.125"),
            )
        )
        db.commit()


def _fill(
    *,
    bot_id: int,
    ts: datetime,
    side: str,
    amount_native: Decimal,
    fee_native: Decimal,
) -> Trade:
    """Build one paper fill. ``tx_sig`` stays null."""
    return Trade(
        bot_id=bot_id,
        chain="solana",
        coin_address=BOBCOIN,
        side=side,
        amount_native=amount_native,
        price_native=Decimal("0.00003"),
        price_usd=Decimal("0.004"),
        mcap_usd=Decimal(11000),
        fee_native=fee_native,
        price_impact_pct=Decimal("1.5"),
        tx_sig=None,
        paper=True,
        actor_id="system",
        ts=ts,
    )


def _trade_count() -> int:
    """Return how many trade rows exist."""
    with _session() as db:
        count = db.scalar(select(func.count()).select_from(Trade))
        assert count is not None
        return count


def _session() -> Session:
    """Open a short-lived session on the test database."""
    return Session(create_engine())
