"""Coin upsert, tick insert, and quote freshness without a network call."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.chains.base import CoinState
from coinwatch.db.models import Coin, Tick
from coinwatch.db.session import create_engine
from coinwatch.services.poll import is_stale, poll_once

BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"


class _FixedAdapter:
    """Chain adapter that returns one snapshot and records calls."""

    id = "solana"
    native_symbol = "SOL"

    def __init__(self, state: CoinState) -> None:
        self._state = state
        self.calls: list[str] = []

    def get_coin_state(self, coin_address: str) -> CoinState:
        self.calls.append(coin_address)
        return self._state


def test_poll_once_upserts_coin_and_appends_ticks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first poll writes the coin and a tick. The second adds a tick only."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")

    state = CoinState(
        price_native=Decimal("0.00003"),
        price_usd=Decimal("0.0045"),
        mcap_usd=Decimal(4500000),
        liquidity_native=Decimal("5.5"),
        curve_pct=Decimal(25),
        volume_1m=Decimal("1.25"),
        volume_5m=Decimal("2.50"),
        volume_15m=Decimal("3.75"),
        holders=None,
        complete=False,
        virtual_sol_reserves=Decimal(30),
        virtual_token_reserves=Decimal(1_000_000),
    )
    adapter = _FixedAdapter(state)
    first = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    second = datetime(2026, 10, 5, 12, 0, 5, tzinfo=UTC)
    engine = create_engine(url)
    with Session(engine) as session:
        poll_once(
            session,
            adapter,
            "solana",
            BOBCOIN,
            name="BobCoin",
            symbol="BOB",
            now=first,
        )
        session.commit()

    with Session(engine) as session:
        poll_once(
            session,
            adapter,
            "solana",
            BOBCOIN,
            name="Bob Coin",
            symbol="BOB",
            now=second,
        )
        session.commit()

    with Session(engine) as session:
        coins = session.scalars(select(Coin)).all()
        ticks = session.scalars(select(Tick).order_by(Tick.ts)).all()
    assert adapter.calls == [BOBCOIN, BOBCOIN]
    assert len(coins) == 1
    assert coins[0].chain == "solana"
    assert coins[0].address == BOBCOIN
    assert coins[0].name == "Bob Coin"
    assert coins[0].symbol == "BOB"
    assert coins[0].created_at == first
    assert coins[0].status == "active"
    assert len(ticks) == 2
    assert [tick.ts for tick in ticks] == [first, second]
    assert ticks[0].price_native == Decimal("0.00003")
    assert isinstance(ticks[0].price_native, Decimal)
    assert not isinstance(ticks[0].price_native, float)
    assert ticks[0].price_usd == Decimal("0.0045")
    assert ticks[0].mcap_usd == Decimal(4500000)
    assert ticks[0].liquidity_native == Decimal("5.5")
    assert ticks[0].curve_pct == Decimal(25)
    assert ticks[0].volume_1m == Decimal("1.25")
    assert ticks[1].volume_5m == Decimal("2.50")
    assert ticks[1].volume_15m == Decimal("3.75")
    assert ticks[1].holders is None
    assert ticks[0].virtual_sol_reserves == Decimal(30)
    assert ticks[0].virtual_token_reserves == Decimal(1_000_000)
    assert ticks[1].virtual_sol_reserves == Decimal(30)
    assert ticks[1].virtual_token_reserves == Decimal(1_000_000)


def test_poll_once_keeps_graduation_after_later_polls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed curve graduates the coin once and later polls leave that stamp."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    adapter = _FixedAdapter(_state(complete=True))
    first = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    second = datetime(2026, 10, 5, 12, 0, 5, tzinfo=UTC)
    third = datetime(2026, 10, 5, 12, 0, 10, tzinfo=UTC)
    engine = create_engine(url)
    with Session(engine) as session:
        poll_once(session, adapter, "solana", BOBCOIN, name="BobCoin", symbol="BOB", now=first)
        session.commit()
    with Session(engine) as session:
        coin = session.get(Coin, ("solana", BOBCOIN))
        assert coin is not None
        assert coin.status == "graduated"
        assert coin.graduated_at == first

        adapter._state = _state(complete=True)
        poll_once(session, adapter, "solana", BOBCOIN, name="BobCoin", symbol="BOB", now=second)
        session.commit()
    with Session(engine) as session:
        coin = session.get(Coin, ("solana", BOBCOIN))
        assert coin is not None
        assert coin.status == "graduated"
        assert coin.graduated_at == first

        adapter._state = _state(complete=False)
        poll_once(session, adapter, "solana", BOBCOIN, name="BobCoin", symbol="BOB", now=third)
        session.commit()
    with Session(engine) as session:
        coin = session.get(Coin, ("solana", BOBCOIN))
        assert coin is not None
        assert coin.status == "graduated"
        assert coin.graduated_at == first


def _state(*, complete: bool) -> CoinState:
    """Return one BobCoin snapshot with the given curve-complete flag."""
    return CoinState(
        price_native=Decimal("0.00003"),
        price_usd=Decimal("0.0045"),
        mcap_usd=Decimal(4500000),
        liquidity_native=Decimal("5.5"),
        curve_pct=Decimal(100) if complete else Decimal(25),
        volume_1m=Decimal(0),
        volume_5m=Decimal(0),
        volume_15m=Decimal(0),
        holders=None,
        complete=complete,
        virtual_sol_reserves=Decimal(30),
        virtual_token_reserves=Decimal(1_000_000),
    )


def test_is_stale_at_sixteen_seconds_and_fresh_at_fourteen() -> None:
    """A tick older than 15 seconds is stale. Fourteen seconds is still fresh."""
    now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    assert is_stale(now - timedelta(seconds=16), now) is True
    assert is_stale(now - timedelta(seconds=14), now) is False
    assert is_stale(now - timedelta(seconds=15), now) is False
    assert is_stale(None, now) is True
