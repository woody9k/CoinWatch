"""CLI quotes against a stored tick. No network and no private key."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from coinwatch.cli import main
from coinwatch.db.models import Coin, Tick
from coinwatch.db.session import create_engine

BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"


def test_cli_prints_a_quote_or_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fresh tick prints quote JSON. A thin balance exits 2 with an error code."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("COINWATCH_KILL_SWITCH", "false")
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    now = datetime.now(UTC)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(
            Coin(
                chain="solana",
                address=BOBCOIN,
                name="BobCoin",
                symbol="BOB",
                created_at=now,
                status="active",
            )
        )
        session.add(
            Tick(
                chain="solana",
                coin_address=BOBCOIN,
                ts=now,
                price_native=Decimal("0.00003"),
                price_usd=Decimal("0.0045"),
                mcap_usd=Decimal(4500000),
                liquidity_native=Decimal("5.5"),
                curve_pct=Decimal(25),
                volume_1m=Decimal(0),
                volume_5m=Decimal(0),
                volume_15m=Decimal(0),
                holders=None,
                virtual_sol_reserves=Decimal(30),
                virtual_token_reserves=Decimal(1_000_000),
            )
        )
        session.commit()

    assert main(["quote-buy", "--amount", "1", "--balance", "10"]) == 0
    quote = json.loads(capsys.readouterr().out)
    assert quote["side"] == "buy"
    assert quote["expected_out"] == "31945.788964181994191674"
    assert quote["fee_native"] == "0.01"
    assert quote["sol_debited"] == "1"
    assert quote["sol_credited"] == "0"

    assert main(["quote-buy", "--amount", "1", "--balance", "1.04"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["error"]["code"] == "fee_reserve"


def test_cli_refuses_a_graduated_coin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A graduated coin is quoted as complete and exits 2 with venue_migrated."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("COINWATCH_KILL_SWITCH", "false")
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    now = datetime.now(UTC)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(
            Coin(
                chain="solana",
                address=BOBCOIN,
                name="BobCoin",
                symbol="BOB",
                created_at=now,
                graduated_at=now,
                status="graduated",
            )
        )
        session.add(
            Tick(
                chain="solana",
                coin_address=BOBCOIN,
                ts=now,
                price_native=Decimal("0.00003"),
                price_usd=Decimal("0.0045"),
                mcap_usd=Decimal(4500000),
                liquidity_native=Decimal("5.5"),
                curve_pct=Decimal(100),
                volume_1m=Decimal(0),
                volume_5m=Decimal(0),
                volume_15m=Decimal(0),
                holders=None,
                virtual_sol_reserves=Decimal(30),
                virtual_token_reserves=Decimal(1_000_000),
            )
        )
        session.commit()

    assert main(["quote-buy", "--amount", "1", "--balance", "10"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["error"]["code"] == "venue_migrated"
