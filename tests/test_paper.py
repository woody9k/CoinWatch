"""Paper fills on a temporary migrated database. No network and no keys."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coinwatch.db.models import AuditEvent, Bot, Coin, Position, Strategy, Trade, User, Wallet
from coinwatch.db.session import create_engine
from coinwatch.errors import QuoteRejected
from coinwatch.quoting import quote_buy, quote_sell
from coinwatch.services.paper import apply_paper_fill

BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
_NOW = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)
_SELL_TOKENS = Decimal(10_000)


def test_paper_buy_then_sell_updates_position_and_audits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paper buy then a partial sell moves size and realized P&L and audits both."""
    session = _session(tmp_path, monkeypatch)
    bot = _paper_bot(session)
    buy = quote_buy(Decimal(1), **_market())
    apply_paper_fill(session, bot, buy, now=_NOW)
    session.commit()
    position = session.get(Position, bot.id)
    assert position is not None
    assert position.size == buy.expected_out
    assert position.cost_native == Decimal(1)
    assert position.realized_pnl_native == Decimal(0)

    size_before = position.size
    cost_before = position.cost_native
    sell = quote_sell(_SELL_TOKENS, position_size=size_before, **_market())
    apply_paper_fill(session, bot, sell, now=_NOW)
    session.commit()
    session.refresh(position)
    cost_removed = cost_before * (_SELL_TOKENS / size_before)
    assert position.size == size_before - _SELL_TOKENS
    assert position.realized_pnl_native == sell.sol_credited - cost_removed
    assert position.cost_native == cost_before - cost_removed

    trades = session.scalars(select(Trade).order_by(Trade.id)).all()
    assert len(trades) == 2
    assert [trade.side for trade in trades] == ["buy", "sell"]
    assert all(trade.paper is True and trade.tx_sig is None for trade in trades)
    assert all(trade.actor_id == "system" for trade in trades)

    audits = session.scalars(select(AuditEvent).where(AuditEvent.action == "trade.submit")).all()
    assert len(audits) == 2
    assert all(row.result == "ok" and row.entity_type == "trade" for row in audits)
    assert all(row.actor_id == "system" and row.actor_type == "system" for row in audits)

    live = Bot(
        chain="solana",
        coin_address=BOBCOIN,
        strategy_id=bot.strategy_id,
        wallet_id=bot.wallet_id,
        status="active",
        paper=False,
        created_by=bot.created_by,
        created_at=_NOW,
    )
    session.add(live)
    session.flush()
    with pytest.raises(QuoteRejected) as raised:
        apply_paper_fill(session, live, buy, now=_NOW)
    assert raised.value.code == "live_disabled"
    assert session.scalar(select(func.count()).select_from(Trade)) == 2
    assert (
        session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "trade.submit")
        )
        == 2
    )
    session.close()


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Upgrade a temporary database and return an open session."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    engine = create_engine(url)
    return Session(engine)


def _paper_bot(session: Session) -> Bot:
    """Insert the coin, user, wallet, strategy, and paper bot the fill needs."""
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


def _market() -> dict[str, object]:
    """Keyword arguments for a fresh quote on 30 SOL and 1_000_000 tokens."""
    return {
        "virtual_sol": Decimal(30),
        "virtual_token": Decimal(1_000_000),
        "observed_at": _NOW,
        "now": _NOW,
        "complete": False,
        "wallet_balance_sol": Decimal(10),
    }
