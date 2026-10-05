"""Local users, sessions, and audited user administration.

Passwords are hashed with argon2. The bootstrap admin comes from
``COINWATCH_ADMIN_USER`` and ``COINWATCH_ADMIN_PASSWORD``. This module does
not log those values. Callers own the transaction: these functions flush and
do not commit.
"""

import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.authz import ROLES, require
from coinwatch.db.models import User, UserSession
from coinwatch.errors import NotAuthorized
from coinwatch.services.audit import record_audit
from coinwatch.settings import get_settings

_HASHER = PasswordHasher()
_SESSION_TTL = timedelta(days=7)


def ensure_admin(session: Session) -> User | None:
    """Insert the bootstrap admin when the env user is missing.

    When ``COINWATCH_ADMIN_USER`` and ``COINWATCH_ADMIN_PASSWORD`` are both
    set and that username does not exist, insert an admin with an argon2
    hash. An existing user is left unchanged, including the password. Empty
    settings insert nothing. Returns the user, or ``None`` when seeding is
    skipped. Does not commit.
    """
    settings = get_settings()
    username = settings.coinwatch_admin_user.strip()
    password = settings.coinwatch_admin_password.get_secret_value()
    if username == "" or password == "":
        return None
    existing = session.scalar(select(User).where(User.username == username))
    if existing is not None:
        return existing
    user = User(
        username=username,
        password_hash=_hash_password(password),
        role="admin",
        created_at=datetime.now(UTC),
    )
    session.add(user)
    session.flush()
    return user


def authenticate(session: Session, username: str, password: str) -> User | None:
    """Return the user when the password matches and the account is active.

    A missing user, a wrong password, or a disabled user returns ``None``.
    This function does not create a session.
    """
    user = session.scalar(select(User).where(User.username == username))
    if user is None or user.disabled_at is not None:
        return None
    if not _password_matches(user.password_hash, password):
        return None
    return user


def create_session(session: Session, user: User) -> str:
    """Store a new session and return its unguessable id.

    The id is ``token_urlsafe`` and is the value a later cookie will hold.
    Expiry is seven days. A disabled user does not get a session. Does not
    commit.
    """
    if user.disabled_at is not None:
        raise NotAuthorized("login")
    now = datetime.now(UTC)
    session_id = secrets.token_urlsafe(32)
    session.add(
        UserSession(
            id=session_id,
            user_id=user.id,
            expires_at=now + _SESSION_TTL,
            created_at=now,
        )
    )
    session.flush()
    return session_id


def get_session_user(session: Session, session_id: str) -> User | None:
    """Return the user for a live session.

    Expired sessions and disabled users return ``None``. Expired rows are
    left in place so a later audit can still see them.
    """
    row = session.get(UserSession, session_id)
    if row is None or row.expires_at <= datetime.now(UTC):
        return None
    user = session.get(User, row.user_id)
    if user is None or user.disabled_at is not None:
        return None
    return user


def require_permission(
    session: Session,
    actor: User,
    permission: str,
    *,
    entity_type: str,
    entity_id: str,
    request_id: str = "",
) -> None:
    """Allow ``permission`` or audit ``auth.denied`` and raise.

    The denial row is flushed into the caller's transaction with result
    ``denied``. This function does not commit.
    """
    try:
        require(actor, permission)
    except NotAuthorized:
        record_audit(
            session,
            actor_type="user",
            actor_id=str(actor.id),
            action="auth.denied",
            entity_type=entity_type,
            entity_id=entity_id,
            result="denied",
            before={},
            after={},
            request_id=request_id,
            detail=permission,
        )
        raise


def set_role(session: Session, actor: User, target: User, role: str) -> None:
    """Change ``target`` to ``role`` when ``actor`` has ``users.manage``.

    Writes an ``ok`` audit row in this transaction. The caller commits.
    A denied check writes ``auth.denied`` and does not change ``target``.
    """
    require_permission(
        session,
        actor,
        "users.manage",
        entity_type="user",
        entity_id=str(target.id),
    )
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    before = {"role": target.role}
    target.role = role
    record_audit(
        session,
        actor_type="user",
        actor_id=str(actor.id),
        action="user.role",
        entity_type="user",
        entity_id=str(target.id),
        result="ok",
        before=before,
        after={"role": role},
    )


def disable_user(session: Session, actor: User, target: User) -> None:
    """Set ``target.disabled_at`` when ``actor`` has ``users.manage``.

    Writes an ``ok`` audit row in this transaction. The caller commits.
    A denied check writes ``auth.denied`` and does not change ``target``.
    """
    require_permission(
        session,
        actor,
        "users.manage",
        entity_type="user",
        entity_id=str(target.id),
    )
    disabled_at = datetime.now(UTC)
    before = {"disabled_at": _timestamp(target.disabled_at)}
    target.disabled_at = disabled_at
    record_audit(
        session,
        actor_type="user",
        actor_id=str(actor.id),
        action="user.disable",
        entity_type="user",
        entity_id=str(target.id),
        result="ok",
        before=before,
        after={"disabled_at": disabled_at.isoformat()},
    )


def _hash_password(password: str) -> str:
    """Return an argon2 hash. The password is not logged."""
    return _HASHER.hash(password)


def _password_matches(password_hash: str, password: str) -> bool:
    """Return whether ``password`` matches ``password_hash``."""
    try:
        return _HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _timestamp(value: datetime | None) -> str | None:
    """Render a UTC timestamp for an audit snapshot."""
    if value is None:
        return None
    return value.isoformat()
