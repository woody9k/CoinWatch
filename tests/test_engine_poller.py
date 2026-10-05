"""Poller configuration and the post-poll bot step.

Importing the engine does not construct an RPC client. These tests pass a
fake chain adapter and never start the five-second loop.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from coinwatch.chains.base import CoinState
from coinwatch.db.models import Bot, Coin, Decision, Strategy, Tick, Trade, User, Wallet
from coinwatch.db.session import create_engine, session_factory
from coinwatch.engine.__main__ import poll_and_step, poller_configured
from coinwatch.errors import ChainReadError
from coinwatch.services.bot_run import step_running_bots
from coinwatch.settings import Settings

BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
_NOW = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)
_LATER = datetime(2026, 10, 5, 22, 0, 5, tzinfo=UTC)
_BUY = """\
rules:
  - trigger: mcap_usd > 10000
    action: buy
    amount_native: 0.05
"""


def test_poller_stays_off_without_rpc_or_sol_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty RPC URL or an empty SOL price does not start the poller."""
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    monkeypatch.delenv("COINWATCH_SOL_USD", raising=False)
    assert poller_configured(Settings(_env_file=None)) is False

    monkeypatch.setenv("SOLANA_RPC_URL", "http://127.0.0.1:9")
    assert poller_configured(Settings(_env_file=None)) is False

    monkeypatch.setenv("COINWATCH_SOL_USD", "   ")
    unset = Settings(_env_file=None)
    assert unset.sol_usd is None
    assert poller_configured(unset) is False

    monkeypatch.setenv("COINWATCH_SOL_USD", "150")
    ready = Settings(_env_file=None)
    assert ready.sol_usd == Decimal(150)
    assert ready.solana_rpc_url == "http://127.0.0.1:9"
    assert poller_configured(ready) is True


def test_bots_step_only_after_the_tick_is_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful poll commits the tick before running bots can see it."""
    factory, engine, bot_id = _world(tmp_path, monkeypatch)
    seen: list[int] = []
    real = step_running_bots

    def _after_commit(
        session: Session,
        chain: str,
        coin_address: str,
        *,
        now: datetime,
    ) -> list[Decision]:
        with Session(engine) as check:
            stored = check.scalar(select(func.count()).select_from(Tick))
        assert stored == 1
        seen.append(1)
        return real(session, chain, coin_address, now=now)

    monkeypatch.setattr("coinwatch.engine.__main__.step_running_bots", _after_commit)
    poll_and_step(
        factory,
        _Snapshot(),
        "solana",
        BOBCOIN,
        name="BobCoin",
        symbol="BOB",
        now=_NOW,
    )
    assert seen == [1]
    with Session(engine) as check:
        trades = check.scalars(select(Trade)).all()
        assert len(trades) == 1
        assert trades[0].bot_id == bot_id
        assert trades[0].paper is True
        outcomes = check.scalars(select(Decision.outcome)).all()
        assert outcomes == ["filled"]


def test_chain_read_error_does_not_step_bots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed poll leaves no tick and does not call the bot step."""
    factory, engine, _bot_id = _world(tmp_path, monkeypatch)
    called: list[str] = []

    def _forbidden(*_args: object, **_kwargs: object) -> list[Decision]:
        called.append("step")
        return []

    monkeypatch.setattr("coinwatch.engine.__main__.step_running_bots", _forbidden)
    poll_and_step(
        factory,
        _FailingAdapter(),
        "solana",
        BOBCOIN,
        name="BobCoin",
        symbol="BOB",
        now=_NOW,
    )
    assert called == []
    with Session(engine) as check:
        assert check.scalar(select(func.count()).select_from(Tick)) == 0
        assert check.scalar(select(func.count()).select_from(Trade)) == 0


def test_step_failure_leaves_the_tick_and_a_later_poll_can_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A step error does not undo the tick or prevent the next poll from filling."""
    factory, engine, bot_id = _world(tmp_path, monkeypatch)
    calls = {"n": 0}
    real = step_running_bots

    def _fail_once(
        session: Session,
        chain: str,
        coin_address: str,
        *,
        now: datetime,
    ) -> list[Decision]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("do-not-persist-this-token")
        return real(session, chain, coin_address, now=now)

    monkeypatch.setattr("coinwatch.engine.__main__.step_running_bots", _fail_once)
    poll_and_step(
        factory,
        _Snapshot(),
        "solana",
        BOBCOIN,
        name="BobCoin",
        symbol="BOB",
        now=_NOW,
    )
    poll_and_step(
        factory,
        _Snapshot(),
        "solana",
        BOBCOIN,
        name="BobCoin",
        symbol="BOB",
        now=_LATER,
    )
    with Session(engine) as check:
        assert check.scalar(select(func.count()).select_from(Tick)) == 2
        trades = check.scalars(select(Trade)).all()
        assert len(trades) == 1
        assert trades[0].bot_id == bot_id
        details = check.scalars(select(Decision.detail)).all()
        assert "do-not-persist-this-token" not in details


class _Snapshot:
    """Chain adapter that returns one fresh BobCoin curve."""

    id = "solana"
    native_symbol = "SOL"

    def get_coin_state(self, coin_address: str) -> CoinState:
        del coin_address
        return CoinState(
            price_native=Decimal("0.00003"),
            price_usd=Decimal(1),
            mcap_usd=Decimal(20000),
            liquidity_native=Decimal(30),
            curve_pct=Decimal(40),
            volume_1m=Decimal(0),
            volume_5m=Decimal(0),
            volume_15m=Decimal(0),
            holders=None,
            complete=False,
            virtual_sol_reserves=Decimal(30),
            virtual_token_reserves=Decimal(1_000_000),
        )


class _FailingAdapter:
    """Chain adapter that fails before a tick can be stored."""

    id = "solana"
    native_symbol = "SOL"

    def get_coin_state(self, coin_address: str) -> CoinState:
        raise ChainReadError("rpc", coin_address)


def _world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[sessionmaker[Session], Engine, int]:
    """Upgrade a temporary database and insert one running BobCoin bot."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("COINWATCH_KILL_SWITCH", "false")
    monkeypatch.setenv("COINWATCH_PAPER_BALANCE_SOL", "10")
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    engine = create_engine(url)
    with Session(engine) as session:
        user = User(
            username="ada",
            password_hash="not-a-password-hash",
            role="admin",
            created_at=_NOW,
        )
        session.add(user)
        session.add(
            Coin(
                chain="solana",
                address=BOBCOIN,
                name="BobCoin",
                symbol="BOB",
                created_at=_NOW,
                status="active",
            )
        )
        session.flush()
        wallet = Wallet(
            chain="solana",
            label="paper",
            public_address="FakeWallet111111111111111111111111111111111",
            created_by=user.id,
            created_at=_NOW,
        )
        strategy = Strategy(
            name="paper",
            yaml_config=_BUY,
            created_by=user.id,
            created_at=_NOW,
            updated_at=_NOW,
        )
        session.add(wallet)
        session.add(strategy)
        session.flush()
        bot = Bot(
            chain="solana",
            coin_address=BOBCOIN,
            strategy_id=strategy.id,
            wallet_id=wallet.id,
            status="running",
            paper=True,
            created_by=user.id,
            created_at=_NOW,
        )
        session.add(bot)
        session.commit()
        bot_id = bot.id
    return session_factory(engine), engine, bot_id
