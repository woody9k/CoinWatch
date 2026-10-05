"""Running bots stepped after a tick. No network and no signal-cli."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coinwatch.db.models import Bot, Coin, Decision, Strategy, Tick, Trade, User, Wallet
from coinwatch.db.session import create_engine
from coinwatch.services.bot_run import step_running_bots
from coinwatch.services.bot_step import step_bot

BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
_NOW = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)
_BUY = """\
rules:
  - trigger: mcap_usd > 10000
    action: buy
    amount_native: 0.05
"""


def test_two_running_bots_both_fill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two running paper bots on BobCoin each fill once after a committed tick."""
    session = _session(tmp_path, monkeypatch)
    user, wallet = _owner(session)
    first = _bot(session, user, wallet, yaml_config=_BUY, name="first")
    second = _bot(session, user, wallet, yaml_config=_BUY, name="second")
    _tick(session)
    session.commit()

    decisions = step_running_bots(session, "solana", BOBCOIN, now=_NOW)
    assert [item.outcome for item in decisions] == ["filled", "filled"]
    assert [item.bot_id for item in decisions] == [first.id, second.id]

    engine = session.get_bind()
    session.close()
    with Session(engine) as check:
        trades = check.scalars(select(Trade).order_by(Trade.id)).all()
        assert [trade.bot_id for trade in trades] == [first.id, second.id]
        assert [trade.paper for trade in trades] == [True, True]
        assert _count(check, Decision) == 2


def test_invalid_strategy_does_not_block_a_valid_buy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A strategy whose text is ``eval`` is rejected and the next bot still fills."""
    session = _session(tmp_path, monkeypatch)
    user, wallet = _owner(session)
    invalid = _bot(session, user, wallet, yaml_config="eval", name="invalid")
    valid = _bot(session, user, wallet, yaml_config=_BUY, name="valid")
    _tick(session)
    session.commit()

    decisions = step_running_bots(session, "solana", BOBCOIN, now=_NOW)
    assert [(item.bot_id, item.outcome) for item in decisions] == [
        (invalid.id, "rejected"),
        (valid.id, "filled"),
    ]

    engine = session.get_bind()
    session.close()
    with Session(engine) as check:
        trades = check.scalars(select(Trade)).all()
        assert len(trades) == 1
        assert trades[0].bot_id == valid.id
        assert trades[0].paper is True


def test_paused_bot_is_skipped_and_running_bot_fills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paused bot is left untouched while a running bot on the same coin fills."""
    session = _session(tmp_path, monkeypatch)
    user, wallet = _owner(session)
    paused = _bot(session, user, wallet, yaml_config=_BUY, name="paused", status="paused")
    running = _bot(session, user, wallet, yaml_config=_BUY, name="running")
    _tick(session)
    session.commit()

    decisions = step_running_bots(session, "solana", BOBCOIN, now=_NOW)
    assert [(item.bot_id, item.outcome) for item in decisions] == [(running.id, "filled")]
    assert paused.armed_rules is None

    engine = session.get_bind()
    session.close()
    with Session(engine) as check:
        trades = check.scalars(select(Trade)).all()
        assert len(trades) == 1
        assert trades[0].bot_id == running.id
        paused_row = check.get(Bot, paused.id)
        assert paused_row is not None
        assert paused_row.status == "paused"
        assert paused_row.armed_rules is None
        assert _count(check, Decision) == 1


def test_raising_bot_rolls_back_and_the_next_bot_fills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A step that raises is dropped, and the following running bot still fills."""
    session = _session(tmp_path, monkeypatch)
    user, wallet = _owner(session)
    raiser = _bot(session, user, wallet, yaml_config=_BUY, name="raiser")
    valid = _bot(session, user, wallet, yaml_config=_BUY, name="valid")
    _tick(session)
    session.commit()
    secret = "do-not-persist-this-token"

    def _raise_for_one(bound: Session, bot: Bot, *, now: datetime) -> Decision | None:
        if bot.id == raiser.id:
            bound.add(
                Decision(
                    bot_id=bot.id,
                    ts=now,
                    rule="",
                    outcome="filled",
                    detail=secret,
                )
            )
            bound.flush()
            raise RuntimeError(secret)
        return step_bot(bound, bot, now=now)

    monkeypatch.setattr("coinwatch.services.bot_run.step_bot", _raise_for_one)
    decisions = step_running_bots(session, "solana", BOBCOIN, now=_NOW)
    assert [(item.bot_id, item.outcome) for item in decisions] == [(valid.id, "filled")]

    engine = session.get_bind()
    session.close()
    with Session(engine) as check:
        details = check.scalars(select(Decision.detail)).all()
        assert secret not in details
        trades = check.scalars(select(Trade)).all()
        assert len(trades) == 1
        assert trades[0].bot_id == valid.id
        stored = check.scalars(select(Decision.bot_id).order_by(Decision.id)).all()
        assert stored == [valid.id]


def test_pending_caller_rows_stay_uncommitted_when_no_bot_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No commit runs before the first bot, so a pending tick stays uncommitted."""
    session = _session(tmp_path, monkeypatch)
    user, wallet = _owner(session)
    _bot(session, user, wallet, yaml_config=_BUY, name="paused", status="paused")
    _tick(session)
    session.flush()

    decisions = step_running_bots(session, "solana", BOBCOIN, now=_NOW)
    assert decisions == []

    engine = session.get_bind()
    with Session(engine) as check:
        assert _count(check, Tick) == 0
        assert _count(check, Decision) == 0
    session.rollback()
    session.close()


def _count(session: Session, model: type[Decision] | type[Tick]) -> int:
    """Return the number of rows in ``model``."""
    counted = session.scalar(select(func.count()).select_from(model))
    return 0 if counted is None else counted


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


def _owner(session: Session) -> tuple[User, Wallet]:
    """Insert the coin, one user, and one wallet."""
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
    session.add(wallet)
    session.flush()
    return user, wallet


def _bot(
    session: Session,
    user: User,
    wallet: Wallet,
    *,
    yaml_config: str,
    name: str,
    status: str = "running",
) -> Bot:
    """Insert one BobCoin bot and its strategy."""
    strategy = Strategy(
        name=name,
        yaml_config=yaml_config,
        created_by=user.id,
        created_at=_NOW,
        updated_at=_NOW,
    )
    session.add(strategy)
    session.flush()
    bot = Bot(
        chain="solana",
        coin_address=BOBCOIN,
        strategy_id=strategy.id,
        wallet_id=wallet.id,
        status=status,
        paper=True,
        created_by=user.id,
        created_at=_NOW,
    )
    session.add(bot)
    session.flush()
    return bot


def _tick(session: Session) -> None:
    """Insert one fresh tick a 0.05 SOL buy can fill against."""
    session.add(
        Tick(
            chain="solana",
            coin_address=BOBCOIN,
            ts=_NOW,
            price_native=Decimal("0.00003"),
            price_usd=Decimal(1),
            mcap_usd=Decimal(20000),
            liquidity_native=Decimal(30),
            curve_pct=Decimal(40),
            volume_1m=Decimal(0),
            volume_5m=Decimal(0),
            volume_15m=Decimal(0),
            virtual_sol_reserves=Decimal(30),
            virtual_token_reserves=Decimal(1_000_000),
        )
    )
