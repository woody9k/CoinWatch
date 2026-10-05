"""Identity, permission denials, and audit on a temporary SQLite database."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coinwatch.authz import ALL_PERMISSIONS, permissions_for, require
from coinwatch.db.models import AuditEvent, User, UserSession
from coinwatch.db.session import create_engine
from coinwatch.errors import NotAuthorized
from coinwatch.services.identity import (
    authenticate,
    create_session,
    disable_user,
    ensure_admin,
    get_session_user,
    set_role,
)

FIXTURE_USER = "ada"
FIXTURE_PASSWORD = "fixture-password"


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """Upgrade an empty SQLite file to head and yield a session."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("COINWATCH_ADMIN_USER", FIXTURE_USER)
    monkeypatch.setenv("COINWATCH_ADMIN_PASSWORD", FIXTURE_PASSWORD)
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    engine = create_engine(url)
    with Session(engine) as session:
        yield session


def test_permission_bundles_match_the_spec() -> None:
    """Admin has every permission; viewer and auditor follow section 3."""
    viewer = permissions_for("viewer")
    assert permissions_for("admin") == ALL_PERMISSIONS
    assert "ticks.read" in viewer
    assert "coins.read" in viewer
    assert "audit.read" not in viewer
    assert "users.read" not in viewer
    assert "users.manage" not in viewer
    assert permissions_for("auditor") == viewer | {"audit.read"}
    assert "trades.execute" in permissions_for("trader")
    assert "users.manage" not in permissions_for("trader")
    actor = SimpleNamespace(role="viewer")
    with pytest.raises(NotAuthorized):
        require(actor, "users.manage")


def test_ensure_admin_creates_one_admin_and_is_idempotent(db: Session) -> None:
    """The env admin is inserted once and a second call does not reset the hash."""
    created = ensure_admin(db)
    db.commit()
    assert created is not None
    assert created.username == FIXTURE_USER
    assert created.role == "admin"
    assert created.password_hash.startswith("$argon2")
    assert FIXTURE_PASSWORD not in created.password_hash
    first_hash = created.password_hash

    again = ensure_admin(db)
    db.commit()
    assert again is not None
    assert again.id == created.id
    assert again.password_hash == first_hash
    count = db.scalar(select(func.count()).select_from(User))
    assert count == 1


def test_wrong_password_and_disabled_user_do_not_get_a_session(db: Session) -> None:
    """Failed authentication inserts no session row."""
    ensure_admin(db)
    db.commit()
    assert authenticate(db, FIXTURE_USER, "wrong-password") is None

    user = db.scalar(select(User).where(User.username == FIXTURE_USER))
    assert user is not None
    user.disabled_at = datetime.now(UTC)
    db.commit()
    assert authenticate(db, FIXTURE_USER, FIXTURE_PASSWORD) is None
    assert db.scalars(select(UserSession)).all() == []


def test_session_id_is_a_token_and_resolves(db: Session) -> None:
    """Session ids are unguessable tokens, and get_session_user loads the user."""
    ensure_admin(db)
    db.commit()
    user = authenticate(db, FIXTURE_USER, FIXTURE_PASSWORD)
    assert user is not None
    session_id = create_session(db, user)
    db.commit()

    assert not session_id.isdigit()
    assert any(character.isalpha() for character in session_id)
    row = db.get(UserSession, session_id)
    assert row is not None
    assert row.expires_at - row.created_at == timedelta(days=7)
    resolved = get_session_user(db, session_id)
    assert resolved is not None
    assert resolved.id == user.id


def test_viewer_users_manage_is_denied_and_audited(db: Session) -> None:
    """A viewer denial writes auth.denied and leaves the target unchanged."""
    admin = ensure_admin(db)
    assert admin is not None
    viewer = User(
        username="vera",
        password_hash="not-a-password-hash",
        role="viewer",
        created_at=datetime.now(UTC),
    )
    db.add(viewer)
    db.commit()

    with pytest.raises(NotAuthorized) as raised:
        set_role(db, viewer, admin, "trader")
    db.commit()

    db.refresh(admin)
    assert admin.role == "admin"
    assert raised.value.permission == "users.manage"
    event = db.scalars(select(AuditEvent)).one()
    assert event.action == "auth.denied"
    assert event.result == "denied"
    assert event.actor_id == str(viewer.id)
    assert event.entity_id == str(admin.id)
    assert FIXTURE_PASSWORD not in event.detail


def test_admin_disable_user_sets_disabled_at_and_audits_ok(db: Session) -> None:
    """An admin disable writes disabled_at and an ok audit row."""
    admin = ensure_admin(db)
    assert admin is not None
    target = User(
        username="vera",
        password_hash="not-a-password-hash",
        role="viewer",
        created_at=datetime.now(UTC),
    )
    db.add(target)
    db.commit()

    disable_user(db, admin, target)
    db.commit()

    db.refresh(target)
    assert target.disabled_at is not None
    event = db.scalars(select(AuditEvent)).one()
    assert event.action == "user.disable"
    assert event.result == "ok"
    assert event.actor_id == str(admin.id)
    assert event.entity_id == str(target.id)
    assert event.after_json["disabled_at"] is not None
    assert FIXTURE_PASSWORD not in str(event.before_json)
    assert FIXTURE_PASSWORD not in str(event.after_json)
