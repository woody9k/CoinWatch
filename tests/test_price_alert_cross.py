"""Market-cap crossings insert an unsent alert. These tests do not use the network."""

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coinwatch.db.models import Alert, AuditEvent, Coin, PriceAlert, Tick, User
from coinwatch.db.session import create_engine
from coinwatch.services import price_cross
from coinwatch.services.price_cross import record_price_alert_crossings

BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
_START = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)


def test_above_cross_inserts_one_unsent_alert_and_does_not_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """9000 to 11000 records one alert. 12000 and 8000 do not record again."""
    session = _session(tmp_path, monkeypatch)
    alert_id = _price_alert(session, "mcap_usd_above", Decimal(10000), enabled=True)
    _tick(session, 0, Decimal(9000))
    session.commit()

    crossed = _tick(session, 1, Decimal(11000))
    recorded = record_price_alert_crossings(session, "solana", BOBCOIN, crossed)
    session.commit()
    assert len(recorded) == 1
    stored = session.scalars(select(Alert)).all()
    assert len(stored) == 1
    alert = stored[0]
    assert alert.chain == "solana"
    assert alert.coin_address == BOBCOIN
    assert alert.type == "price_alert"
    assert alert.message == "mcap_usd_above 10000 11000"
    assert alert.sent_at is None
    assert alert.escalated is False
    audits = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "price_alert.cross")
    ).all()
    assert len(audits) == 1
    audit = audits[0]
    assert audit.result == "ok"
    assert audit.entity_type == "price_alert"
    assert audit.entity_id == str(alert_id)
    assert audit.after_json == {"mcap_usd": "11000"}
    assert audit.actor_type == "system"
    assert audit.actor_id == "system"

    still_above = _tick(session, 2, Decimal(12000))
    assert record_price_alert_crossings(session, "solana", BOBCOIN, still_above) == []
    session.commit()
    fell = _tick(session, 3, Decimal(8000))
    assert record_price_alert_crossings(session, "solana", BOBCOIN, fell) == []
    session.commit()
    assert session.scalar(select(func.count()).select_from(Alert)) == 1
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "price_alert.cross")
        )
        == 1
    )
    session.close()


def test_below_cross_inserts_one_unsent_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """11000 to 8000 on an mcap_usd_below alert inserts one unsent alert."""
    session = _session(tmp_path, monkeypatch)
    alert_id = _price_alert(session, "mcap_usd_below", Decimal(10000), enabled=True)
    _tick(session, 0, Decimal(11000))
    session.commit()
    crossed = _tick(session, 1, Decimal(8000))
    recorded = record_price_alert_crossings(session, "solana", BOBCOIN, crossed)
    session.commit()
    assert len(recorded) == 1
    alert = session.scalars(select(Alert)).one()
    assert alert.message == "mcap_usd_below 10000 8000"
    assert alert.sent_at is None
    assert alert.escalated is False
    audit = session.scalars(select(AuditEvent)).one()
    assert audit.action == "price_alert.cross"
    assert audit.entity_id == str(alert_id)
    assert audit.after_json == {"mcap_usd": "8000"}
    session.close()


def test_disabled_alert_inserts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A disabled above alert does not record when market cap crosses it."""
    session = _session(tmp_path, monkeypatch)
    _price_alert(session, "mcap_usd_above", Decimal(10000), enabled=False)
    _tick(session, 0, Decimal(9000))
    session.commit()
    crossed = _tick(session, 1, Decimal(11000))
    assert record_price_alert_crossings(session, "solana", BOBCOIN, crossed) == []
    session.commit()
    assert session.scalar(select(func.count()).select_from(Alert)) == 0
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0
    session.close()


def test_equal_to_the_threshold_is_not_a_cross(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Landing on the threshold does not record an above or below alert."""
    session = _session(tmp_path, monkeypatch)
    _price_alert(session, "mcap_usd_above", Decimal(10000), enabled=True)
    _price_alert(session, "mcap_usd_below", Decimal(10000), enabled=True)
    _tick(session, 0, Decimal(9000))
    session.commit()
    landed = _tick(session, 1, Decimal(10000))
    assert record_price_alert_crossings(session, "solana", BOBCOIN, landed) == []
    session.commit()
    assert session.scalar(select(func.count()).select_from(Alert)) == 0
    session.close()


def test_first_tick_and_other_conditions_record_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No previous tick, or a condition other than above or below, records nothing."""
    session = _session(tmp_path, monkeypatch)
    _price_alert(session, "curve_pct", Decimal(10000), enabled=True)
    first = _tick(session, 0, Decimal(9000))
    assert record_price_alert_crossings(session, "solana", BOBCOIN, first) == []
    session.commit()
    later = _tick(session, 1, Decimal(11000))
    assert record_price_alert_crossings(session, "solana", BOBCOIN, later) == []
    session.commit()
    assert session.scalar(select(func.count()).select_from(Alert)) == 0
    session.close()


def test_recording_does_not_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A crossing stays uncommitted until the caller commits."""
    session = _session(tmp_path, monkeypatch)
    _price_alert(session, "mcap_usd_above", Decimal(10000), enabled=True)
    _tick(session, 0, Decimal(9000))
    session.commit()
    crossed = _tick(session, 1, Decimal(11000))
    recorded = record_price_alert_crossings(session, "solana", BOBCOIN, crossed)
    assert len(recorded) == 1
    session.rollback()
    assert session.scalar(select(func.count()).select_from(Alert)) == 0
    session.close()


def test_price_cross_module_does_not_import_signal_cli() -> None:
    """The crossing service does not import signal-cli or an alert sender."""
    source = Path(price_cross.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    assert all("signal_cli" not in name for name in modules)
    assert "signal_cli" not in source
    assert "AlertSender" not in source


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Upgrade a temporary database and return an open session."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    return Session(create_engine(url))


def _price_alert(session: Session, condition: str, threshold: Decimal, *, enabled: bool) -> int:
    """Insert the coin, one user if needed, and a price alert. Return its id."""
    if session.get(Coin, ("solana", BOBCOIN)) is None:
        user = User(
            username="ada",
            password_hash="not-a-password-hash",
            role="admin",
            created_at=_START,
        )
        session.add(user)
        session.add(
            Coin(
                chain="solana",
                address=BOBCOIN,
                name="BobCoin",
                symbol="BOB",
                created_at=_START,
                status="active",
            )
        )
        session.flush()
    else:
        user = session.scalars(select(User)).one()
    alert = PriceAlert(
        chain="solana",
        coin_address=BOBCOIN,
        condition=condition,
        threshold=threshold,
        enabled=enabled,
        created_by=user.id,
    )
    session.add(alert)
    session.flush()
    return alert.id


def _tick(session: Session, step: int, mcap_usd: Decimal) -> Tick:
    """Insert one BobCoin tick ``step`` minutes after the start."""
    tick = Tick(
        chain="solana",
        coin_address=BOBCOIN,
        ts=_START + timedelta(minutes=step),
        price_native=Decimal("0.00003"),
        price_usd=Decimal(1),
        mcap_usd=mcap_usd,
        liquidity_native=Decimal(30),
        curve_pct=Decimal(40),
        volume_1m=Decimal(0),
        volume_5m=Decimal(0),
        volume_15m=Decimal(0),
        holders=None,
    )
    session.add(tick)
    session.flush()
    return tick
