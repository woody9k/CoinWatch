"""Money columns round-trip as exact decimals stored as SQLite text."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from coinwatch.db.models import Bot, Coin, Strategy, Tick, Trade, User, Wallet
from coinwatch.db.session import create_engine

BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
_NOW = datetime(2026, 10, 5, 23, 0, tzinfo=UTC)
_AMOUNT = Decimal("0.2")
_RESERVES = Decimal("31945.788964181994191674")


def test_money_round_trips_as_decimal_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A trade amount and a tick reserve reload as the same decimals, stored as text."""
    session = _session(tmp_path, monkeypatch)
    bot = _bot(session)
    trade = Trade(
        bot_id=bot.id,
        chain="solana",
        coin_address=BOBCOIN,
        side="buy",
        amount_native=_AMOUNT,
        price_native=Decimal("0.00003"),
        price_usd=Decimal("0.004"),
        mcap_usd=Decimal(11000),
        fee_native=Decimal("0.05"),
        price_impact_pct=Decimal("1.5"),
        tx_sig=None,
        paper=True,
        actor_id="system",
        ts=_NOW,
    )
    tick = Tick(
        chain="solana",
        coin_address=BOBCOIN,
        ts=_NOW,
        price_native=Decimal("0.00003"),
        price_usd=Decimal("0.004"),
        mcap_usd=Decimal(11000),
        liquidity_native=Decimal("5.5"),
        curve_pct=Decimal("40.5"),
        volume_1m=Decimal(1),
        volume_5m=Decimal(2),
        volume_15m=Decimal(3),
        holders=None,
        virtual_sol_reserves=Decimal(30),
        virtual_token_reserves=_RESERVES,
    )
    session.add(trade)
    session.add(tick)
    session.commit()
    session.expire_all()

    reloaded_trade = session.get(Trade, trade.id)
    reloaded_tick = session.get(Tick, tick.id)
    assert reloaded_trade is not None
    assert reloaded_tick is not None
    assert reloaded_trade.amount_native == _AMOUNT
    assert reloaded_tick.virtual_token_reserves == _RESERVES
    assert session.scalar(text("SELECT typeof(amount_native) FROM trades")) == "text"
    assert session.scalar(text("SELECT typeof(virtual_token_reserves) FROM ticks")) == "text"
    session.close()


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Upgrade a temporary database and return an open session."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    return Session(create_engine(url))


def _bot(session: Session) -> Bot:
    """Insert the coin, user, wallet, strategy, and paper bot a trade requires."""
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
        yaml_config="name: paper\n",
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
        status="active",
        paper=True,
        created_by=user.id,
        created_at=_NOW,
    )
    session.add(bot)
    session.flush()
    return bot
