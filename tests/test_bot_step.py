"""Paper bot steps on a temporary migrated database. No network and no keys."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coinwatch.db.models import Bot, Coin, Decision, Position, Strategy, Tick, Trade, User, Wallet
from coinwatch.db.session import create_engine
from coinwatch.services.bot_step import step_bot

BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
_NOW = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)
_BUY = """\
rules:
  - trigger: mcap_usd > 10000
    action: buy
    amount_native: 0.05
"""
_SELL_ALL = """\
rules:
  - trigger: mcap_usd > 10000
    action: sell_all
"""
_DEV_SOLD = """\
rules:
  - trigger: dev_sold == true
    action: sell_all
"""
_FIVE_MINUTE = """\
rules:
  - trigger: price_change_pct_5m > 20
    action: buy
    amount_native: 0.05
"""


def test_fresh_mcap_rule_fills_once_then_stays_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A running paper bot buys once when market cap crosses, then stays quiet."""
    session, bot = _world(tmp_path, monkeypatch, yaml_config=_BUY)
    _tick(session, ts=_NOW, mcap_usd=Decimal(20000))
    session.commit()

    filled = step_bot(session, bot, now=_NOW)
    session.commit()
    assert filled is not None
    assert filled.outcome == "filled"
    assert filled.rule == "mcap_usd"
    assert filled.detail == "buy"
    assert filled.ts == _NOW
    assert _count(session, Trade) == 1
    trade = session.scalars(select(Trade)).one()
    assert trade.paper is True
    assert trade.side == "buy"
    assert trade.tx_sig is None

    engine = session.get_bind()
    bot_id = bot.id
    session.close()
    with Session(engine) as again:
        stored = again.get(Bot, bot_id)
        assert stored is not None
        assert json.loads(stored.armed_rules or "null") == [0]
        quiet = step_bot(again, stored, now=_NOW)
        again.commit()
        assert quiet is not None
        assert quiet.outcome == "quiet"
        assert quiet.rule == ""
        assert _count(again, Trade) == 1
        outcomes = again.scalars(select(Decision.outcome).order_by(Decision.id)).all()
        assert outcomes == ["filled", "quiet"]
        assert json.loads(stored.armed_rules or "null") == [0]


def test_paused_bot_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A paused bot leaves decisions, trades, and armed rules untouched."""
    session, bot = _world(tmp_path, monkeypatch, yaml_config=_BUY, status="paused")
    _tick(session, ts=_NOW, mcap_usd=Decimal(20000))
    session.commit()
    assert step_bot(session, bot, now=_NOW) is None
    session.commit()
    assert _count(session, Decision) == 0
    assert _count(session, Trade) == 0
    assert bot.armed_rules is None
    session.close()


def test_live_bot_is_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A running bot with paper off records live_disabled and does not trade."""
    session, bot = _world(tmp_path, monkeypatch, yaml_config=_BUY, paper=False)
    _tick(session, ts=_NOW, mcap_usd=Decimal(20000))
    session.commit()
    decision = step_bot(session, bot, now=_NOW)
    session.commit()
    assert decision is not None
    assert decision.outcome == "live_disabled"
    assert decision.rule == ""
    assert decision.detail == "live sends are not implemented"
    assert _count(session, Trade) == 0
    session.close()


def test_sell_all_with_empty_position_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sell_all on a zero-size position is rejected and does not quote a trade."""
    session, bot = _world(tmp_path, monkeypatch, yaml_config=_SELL_ALL)
    _tick(session, ts=_NOW, mcap_usd=Decimal(20000))
    session.add(
        Position(
            bot_id=bot.id,
            chain="solana",
            coin_address=BOBCOIN,
            size=Decimal(0),
            cost_native=Decimal(0),
            realized_pnl_native=Decimal(0),
            updated_at=_NOW,
        )
    )
    session.commit()
    decision = step_bot(session, bot, now=_NOW)
    session.commit()
    assert decision is not None
    assert decision.outcome == "rejected"
    assert decision.detail == "position"
    assert decision.rule == "mcap_usd"
    assert _count(session, Trade) == 0
    session.close()


def test_dev_sold_does_not_fire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dev_sold rule stays quiet because holder and dev scans are not wired."""
    session, bot = _world(tmp_path, monkeypatch, yaml_config=_DEV_SOLD)
    _tick(session, ts=_NOW, mcap_usd=Decimal(20000))
    session.commit()
    decision = step_bot(session, bot, now=_NOW)
    session.commit()
    assert decision is not None
    assert decision.outcome == "quiet"
    assert decision.rule == ""
    assert _count(session, Trade) == 0
    assert json.loads(bot.armed_rules or "null") == []
    session.close()


def test_five_minute_price_rise_buys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 50 percent rise versus the tick from six minutes ago fires a buy."""
    session, bot = _world(tmp_path, monkeypatch, yaml_config=_FIVE_MINUTE)
    _tick(session, ts=_NOW - timedelta(minutes=6), mcap_usd=Decimal(1000), price_native=Decimal(2))
    _tick(session, ts=_NOW, mcap_usd=Decimal(1500), price_native=Decimal(3))
    session.commit()
    decision = step_bot(session, bot, now=_NOW)
    session.commit()
    assert decision is not None
    assert decision.outcome == "filled"
    assert decision.rule == "price_change_pct_5m"
    assert decision.detail == "buy"
    assert _count(session, Trade) == 1
    session.close()


def _count(session: Session, model: type[Decision] | type[Trade]) -> int:
    """Return the number of rows in ``model``."""
    counted = session.scalar(select(func.count()).select_from(model))
    return 0 if counted is None else counted


def _world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    yaml_config: str,
    status: str = "running",
    paper: bool = True,
) -> tuple[Session, Bot]:
    """Upgrade a temporary database and insert one bot bound to BobCoin."""
    session = _session(tmp_path, monkeypatch)
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
        yaml_config=yaml_config,
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
        status=status,
        paper=paper,
        created_by=user.id,
        created_at=_NOW,
    )
    session.add(bot)
    session.flush()
    return session, bot


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Upgrade a temporary database and return an open session."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("COINWATCH_KILL_SWITCH", "false")
    monkeypatch.setenv("COINWATCH_PAPER_BALANCE_SOL", "10")
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    return Session(create_engine(url))


def _tick(
    session: Session,
    *,
    ts: datetime,
    mcap_usd: Decimal,
    price_native: Decimal = Decimal("0.00003"),
) -> None:
    """Insert one fresh tick with reserves a 0.05 SOL buy can fill against."""
    session.add(
        Tick(
            chain="solana",
            coin_address=BOBCOIN,
            ts=ts,
            price_native=price_native,
            price_usd=Decimal(1),
            mcap_usd=mcap_usd,
            liquidity_native=Decimal(30),
            curve_pct=Decimal(40),
            volume_1m=Decimal(0),
            volume_5m=Decimal(0),
            volume_15m=Decimal(0),
            virtual_sol_reserves=Decimal(30),
            virtual_token_reserves=Decimal(1_000_000),
        )
    )
