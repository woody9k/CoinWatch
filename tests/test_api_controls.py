"""Paper wallet, strategy, and bot control routes."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.authz import permissions_for
from coinwatch.db.models import AuditEvent, Bot, Chain, Coin, Strategy, User, Wallet
from coinwatch.db.session import create_engine
from coinwatch.services.identity import create_session

ADMIN_USER = "ada"
ADMIN_PASSWORD = "fixture-password"
CSRF = {"X-CoinWatch-Request": "1"}
BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
PUBLIC = "FakeWallet111111111111111111111111111111111"
VALID_YAML = """
rules:
  - trigger: mcap_usd > 10000
    action: buy
    amount_native: 0.05
"""
BOT_KEYS = {
    "id",
    "chain",
    "coin_address",
    "strategy_id",
    "wallet_id",
    "status",
    "paper",
    "act_on_inference",
}


def test_admin_starts_a_paused_paper_bot(api_client: TestClient) -> None:
    """An admin creates a wallet, strategy, and paused paper bot, then starts it.

    A second start is 409 and the bot stays running. Pause and stop succeed.
    Audit rows exist for the create and start actions, and the bot list omits
    armed rules.
    """
    _seed_coin()
    _login(api_client)
    wallet = api_client.post(
        "/api/wallets",
        json={"chain": "solana", "label": "desk", "public_address": PUBLIC},
        headers=CSRF,
    )
    assert wallet.status_code == 200
    assert wallet.json() == {
        "id": wallet.json()["id"],
        "chain": "solana",
        "label": "desk",
        "public_address": PUBLIC,
    }
    strategy = api_client.post(
        "/api/strategies",
        json={"name": "paper", "yaml_config": VALID_YAML},
        headers=CSRF,
    )
    assert strategy.status_code == 200
    assert strategy.json() == {"id": strategy.json()["id"], "name": "paper"}
    listed = api_client.get("/api/strategies")
    assert listed.status_code == 200
    assert listed.json() == [
        {"id": strategy.json()["id"], "name": "paper", "yaml_config": VALID_YAML}
    ]
    created = api_client.post(
        "/api/bots",
        json={
            "chain": "solana",
            "coin_address": BOBCOIN,
            "strategy_id": strategy.json()["id"],
            "wallet_id": wallet.json()["id"],
        },
        headers=CSRF,
    )
    assert created.status_code == 200
    bot = created.json()
    assert set(bot) == BOT_KEYS
    assert bot["status"] == "paused"
    assert bot["paper"] is True
    assert bot["act_on_inference"] is False
    assert bot["coin_address"] == BOBCOIN
    bot_id = bot["id"]
    started = api_client.post(f"/api/bots/{bot_id}/start", headers=CSRF)
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    again = api_client.post(f"/api/bots/{bot_id}/start", headers=CSRF)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "invalid_status"
    with _session() as db:
        row = db.get(Bot, bot_id)
        assert row is not None
        assert row.status == "running"
    paused = api_client.post(f"/api/bots/{bot_id}/pause", headers=CSRF)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    stopped = api_client.post(f"/api/bots/{bot_id}/stop", headers=CSRF)
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    with _session() as db:
        row = db.get(Bot, bot_id)
        assert row is not None
        row.armed_rules = "[0]"
        db.commit()
    bots = api_client.get("/api/bots")
    assert bots.status_code == 200
    assert set(bots.json()[0]) == BOT_KEYS
    assert bots.json()[0]["status"] == "stopped"
    assert "armed_rules" not in bots.json()[0]
    with _session() as db:
        actions = {
            event.action: event
            for event in db.scalars(select(AuditEvent)).all()
            if event.action
            in {
                "wallet.create",
                "strategy.create",
                "bot.create",
                "bot.start",
                "bot.pause",
                "bot.stop",
            }
        }
        assert set(actions) >= {"wallet.create", "strategy.create", "bot.create", "bot.start"}
        assert actions["wallet.create"].after_json == {
            "id": wallet.json()["id"],
            "chain": "solana",
            "label": "desk",
        }
        assert actions["strategy.create"].after_json == {
            "id": strategy.json()["id"],
            "name": "paper",
        }
        assert actions["bot.create"].after_json == {
            "id": bot_id,
            "chain": "solana",
            "coin_address": BOBCOIN,
            "status": "paused",
            "paper": True,
        }
        assert actions["bot.start"].before_json == {"status": "paused"}
        assert actions["bot.start"].after_json == {"status": "running"}
        assert actions["bot.start"].result == "ok"


def test_strategy_yaml_with_eval_is_rejected(api_client: TestClient) -> None:
    """YAML that contains eval is 422 and does not insert a strategy."""
    _login(api_client)
    response = api_client.post(
        "/api/strategies",
        json={"name": "bad", "yaml_config": "rules: []\neval: true\n"},
        headers=CSRF,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_strategy"
    with _session() as db:
        assert db.scalars(select(Strategy)).all() == []
        created = db.scalars(select(AuditEvent).where(AuditEvent.action == "strategy.create")).all()
        assert created == []


def test_live_bot_is_rejected(api_client: TestClient) -> None:
    """paper false is 422 and does not insert a bot."""
    _seed_coin()
    _login(api_client)
    wallet_id = _wallet_id(api_client)
    strategy_id = _strategy_id(api_client)
    response = api_client.post(
        "/api/bots",
        json={
            "chain": "solana",
            "coin_address": BOBCOIN,
            "strategy_id": strategy_id,
            "wallet_id": wallet_id,
            "paper": False,
        },
        headers=CSRF,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "live_disabled"
    with _session() as db:
        assert db.scalars(select(Bot)).all() == []


def test_wallets_require_a_session(api_client: TestClient) -> None:
    """GET /api/wallets without a session cookie is 401."""
    response = api_client.get("/api/wallets")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_role_without_wallets_read_is_denied(api_client: TestClient) -> None:
    """A role that lacks wallets.read is 403 before any wallet lookup.

    ``permissions_for("viewer")`` includes ``wallets.read`` because that
    permission ends in ``.read``. This caller uses a role that grants nothing.
    """
    assert "wallets.read" not in permissions_for("custom")
    assert "wallets.read" in permissions_for("viewer")
    _use_session(api_client, _session_for_role("custom", "narrow"))
    response = api_client.get("/api/wallets")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_authorized"
    with _session() as db:
        denied = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.denied")).all()
        assert len(denied) == 1
        assert denied[0].result == "denied"
        assert denied[0].detail == "wallets.read"
        assert db.scalars(select(Wallet)).all() == []


def test_viewer_lists_a_seeded_public_wallet(api_client: TestClient) -> None:
    """A viewer sees one public wallet, and the JSON keys are only the public fields."""
    _seed_public_wallet()
    _use_session(api_client, _session_for_role("viewer", "vera"))
    response = api_client.get("/api/wallets")
    assert response.status_code == 200
    listed = response.json()
    assert len(listed) == 1
    wallet = listed[0]
    assert set(wallet) == {"id", "chain", "label", "public_address"}
    assert wallet["chain"] == "solana"
    assert wallet["label"] == "desk"
    assert wallet["public_address"] == PUBLIC
    assert type(wallet["id"]) is int


def test_wallet_list_is_empty(api_client: TestClient) -> None:
    """An authorized caller with no wallets gets an empty list, not 404."""
    _login(api_client)
    response = api_client.get("/api/wallets")
    assert response.status_code == 200
    assert response.json() == []


def test_wallet_private_key_is_rejected(api_client: TestClient) -> None:
    """A wallet body with private_key is 422 and does not insert a wallet."""
    _login(api_client)
    response = api_client.post(
        "/api/wallets",
        json={
            "chain": "solana",
            "label": "desk",
            "public_address": PUBLIC,
            "private_key": "not-a-real-key",
        },
        headers=CSRF,
    )
    assert response.status_code == 422
    with _session() as db:
        assert db.scalars(select(Wallet)).all() == []
        created = db.scalars(select(AuditEvent).where(AuditEvent.action == "wallet.create")).all()
        assert created == []


def test_viewer_bot_create_denial_is_committed(api_client: TestClient) -> None:
    """A viewer POST /api/bots is 403 and the denial audit is committed."""
    _use_session(api_client, _session_for_role("viewer", "vera"))
    response = api_client.post("/api/bots", json={}, headers=CSRF)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_authorized"
    with _session() as db:
        denied = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.denied")).all()
        assert len(denied) == 1
        assert denied[0].result == "denied"
        assert denied[0].detail == "bots.control"
        assert db.scalars(select(Bot)).all() == []


def test_missing_bot_is_not_found_after_permission(api_client: TestClient) -> None:
    """An admin start of a missing bot is 404. A viewer is denied first."""
    _login(api_client)
    missing = api_client.post("/api/bots/404/start", headers=CSRF)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    _use_session(api_client, _session_for_role("viewer", "vera"))
    denied = api_client.post("/api/bots/404/start", headers=CSRF)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "not_authorized"


def test_mutations_without_request_header_insert_nothing(api_client: TestClient) -> None:
    """Mutations without X-CoinWatch-Request are 403 and insert nothing."""
    _seed_coin()
    _login(api_client)
    bodies = {
        "/api/wallets": {"chain": "solana", "label": "desk", "public_address": PUBLIC},
        "/api/strategies": {"name": "paper", "yaml_config": VALID_YAML},
        "/api/bots": {
            "chain": "solana",
            "coin_address": BOBCOIN,
            "strategy_id": 1,
            "wallet_id": 1,
        },
    }
    for path, body in bodies.items():
        response = api_client.post(path, json=body)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "missing_request_header"
    for action in ("start", "pause", "stop"):
        response = api_client.post(f"/api/bots/1/{action}")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "missing_request_header"
    with _session() as db:
        assert db.scalars(select(Wallet)).all() == []
        assert db.scalars(select(Strategy)).all() == []
        assert db.scalars(select(Bot)).all() == []
        writes = db.scalars(
            select(AuditEvent).where(
                AuditEvent.action.in_(
                    [
                        "wallet.create",
                        "strategy.create",
                        "bot.create",
                        "bot.start",
                        "bot.pause",
                        "bot.stop",
                    ]
                )
            )
        ).all()
        assert writes == []


def test_wallet_chain_must_match_the_bot(api_client: TestClient) -> None:
    """A wallet on another chain does not create a bot."""
    _seed_coin()
    _login(api_client)
    wallet_id = _wallet_id(api_client)
    strategy_id = _strategy_id(api_client)
    with _session() as db:
        db.add(Chain(id="other", native_symbol="OTH"))
        admin = db.scalar(select(User).where(User.username == ADMIN_USER))
        assert admin is not None
        other = Wallet(
            chain="other",
            label="other",
            public_address="OtherWallet1111111111111111111111111111111",
            created_by=admin.id,
            created_at=datetime.now(UTC),
        )
        db.add(other)
        db.commit()
        other_id = other.id
    response = api_client.post(
        "/api/bots",
        json={
            "chain": "solana",
            "coin_address": BOBCOIN,
            "strategy_id": strategy_id,
            "wallet_id": other_id,
        },
        headers=CSRF,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    with _session() as db:
        assert db.get(Wallet, wallet_id) is not None
        assert db.scalars(select(Bot)).all() == []


def _wallet_id(client: TestClient) -> int:
    """Create the desk wallet and return its id."""
    response = client.post(
        "/api/wallets",
        json={"chain": "solana", "label": "desk", "public_address": PUBLIC},
        headers=CSRF,
    )
    assert response.status_code == 200
    wallet_id = response.json()["id"]
    assert isinstance(wallet_id, int)
    return wallet_id


def _strategy_id(client: TestClient) -> int:
    """Create a valid strategy and return its id."""
    response = client.post(
        "/api/strategies",
        json={"name": "paper", "yaml_config": VALID_YAML},
        headers=CSRF,
    )
    assert response.status_code == 200
    strategy_id = response.json()["id"]
    assert isinstance(strategy_id, int)
    return strategy_id


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


def _seed_public_wallet() -> None:
    """Insert one public wallet owned by the bootstrap admin."""
    with _session() as db:
        admin = db.scalar(select(User).where(User.username == ADMIN_USER))
        assert admin is not None
        db.add(
            Wallet(
                chain="solana",
                label="desk",
                public_address=PUBLIC,
                created_by=admin.id,
                created_at=datetime.now(UTC),
            )
        )
        db.commit()


def _seed_coin() -> None:
    """Insert BobCoin so a paper bot can reference it."""
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
