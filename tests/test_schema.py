"""Schema migration against a temporary SQLite database."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from coinwatch.db.models import Chain, Coin, Tick
from coinwatch.db.session import create_engine

BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"


def test_upgrade_inserts_chain_coin_and_tick(tmp_path: Path, monkeypatch) -> None:
    """Upgrade an empty file, round-trip a chain, coin, and tick, and use WAL."""
    database_path = tmp_path / "coinwatch.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")

    engine = create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
    assert journal_mode == "wal"

    observed_at = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)
    price = Decimal("1.25")
    with Session(engine) as session:
        seeded = session.get(Chain, "solana")
        assert seeded is not None
        assert seeded.native_symbol == "SOL"

        session.add(Chain(id="local", native_symbol="LOC"))
        session.add(
            Coin(
                chain="local",
                address=BOBCOIN,
                name="BobCoin",
                symbol="BOB",
                created_at=observed_at,
                status="active",
            )
        )
        session.add(
            Tick(
                chain="local",
                coin_address=BOBCOIN,
                ts=observed_at,
                price_native=price,
                price_usd=price,
                mcap_usd=Decimal("10000.00"),
                liquidity_native=Decimal("12.50"),
                curve_pct=Decimal("40.5"),
                volume_1m=Decimal("1.00"),
                volume_5m=Decimal("2.00"),
                volume_15m=Decimal("3.00"),
                holders=42,
            )
        )
        session.commit()

    with Session(engine) as session:
        chain = session.get(Chain, "local")
        coin = session.get(Coin, ("local", BOBCOIN))
        tick = session.scalars(select(Tick)).one()
        assert chain is not None
        assert chain.native_symbol == "LOC"
        assert coin is not None
        assert coin.symbol == "BOB"
        assert coin.created_at == observed_at
        assert coin.created_at.tzinfo is not None
        assert tick.price_native == price
        assert isinstance(tick.price_native, Decimal)
        assert not isinstance(tick.price_native, float)
        assert tick.ts == observed_at
        assert tick.holders == 42
        assert tick.curve_pct == Decimal("40.5")
