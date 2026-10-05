"""Session routes: login cookie, request header, audit, and logout."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.api.app import create_app, request_id_of, request_session
from coinwatch.authz import ALL_PERMISSIONS
from coinwatch.db.models import AuditEvent, User, UserSession
from coinwatch.db.session import create_engine
from coinwatch.services.identity import create_session, get_session_user, require_permission

ADMIN_USER = "ada"
ADMIN_PASSWORD = "fixture-password"
CSRF = {"X-CoinWatch-Request": "1"}


def test_status_does_not_require_a_cookie(api_client: TestClient) -> None:
    """GET /api/status stays public and does not set a session cookie."""
    response = api_client.get("/api/status")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "coinwatch"}
    assert "coinwatch_session" not in response.cookies


def test_login_sets_cookie_and_me_lists_admin_permissions(api_client: TestClient) -> None:
    """A successful login sets the session cookie and /api/me lists admin permissions."""
    response = api_client.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=CSRF,
    )
    assert response.status_code == 200
    assert response.json() == {"username": ADMIN_USER, "role": "admin"}
    assert response.cookies.get("coinwatch_session")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert "secure" not in cookie

    me = api_client.get("/api/me")
    assert me.status_code == 200
    body = me.json()
    assert body["username"] == ADMIN_USER
    assert body["role"] == "admin"
    assert body["permissions"] == sorted(ALL_PERMISSIONS)


def test_login_without_request_header_is_forbidden(api_client: TestClient) -> None:
    """A login without the request header is 403 and does not create a session."""
    response = api_client.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "missing_request_header"
    assert "coinwatch_session" not in response.cookies
    with _session() as db:
        assert db.scalars(select(UserSession)).all() == []
        assert db.scalars(select(AuditEvent)).all() == []


def test_bad_password_is_unauthorized_and_audited(
    api_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A wrong password is 401, writes auth.login denied, and is absent from logs."""
    caplog.set_level(logging.INFO)
    response = api_client.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD + "-wrong"},
        headers=CSRF,
    )
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "invalid_login", "message": "Invalid username or password."}
    }
    with _session() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login")).all()
        assert len(events) == 1
        assert events[0].result == "denied"
        stored = json.dumps(
            {
                "before": events[0].before_json,
                "after": events[0].after_json,
                "detail": events[0].detail,
            }
        )
        assert ADMIN_PASSWORD not in stored
        assert db.scalars(select(UserSession)).all() == []
    logged = [record.getMessage() for record in caplog.records]
    assert logged
    assert all(ADMIN_PASSWORD not in line for line in logged)
    payloads = [json.loads(line) for line in logged if line.startswith("{")]
    assert any(item.get("event") == "http.request" for item in payloads)


def test_logout_then_me_is_unauthorized(api_client: TestClient) -> None:
    """Logout clears the session so the next /api/me is 401."""
    login = api_client.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=CSRF,
    )
    assert login.status_code == 200
    logout = api_client.post("/api/logout", headers=CSRF)
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}
    me = api_client.get("/api/me")
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "unauthenticated"
    with _session() as db:
        logout_rows = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.logout")).all()
        assert len(logout_rows) == 1
        assert logout_rows[0].result == "ok"
        assert db.scalars(select(UserSession)).all() == []


def test_permission_denial_commits_the_audit_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A NotAuthorized response keeps the flushed auth.denied row."""
    database_path = tmp_path / "coinwatch.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("COINWATCH_ADMIN_USER", ADMIN_USER)
    monkeypatch.setenv("COINWATCH_ADMIN_PASSWORD", ADMIN_PASSWORD)
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")

    app = create_app()

    @app.get("/api/probe-deny")
    async def probe_deny(request: Request) -> Response:
        db = request_session(request)
        assert db is not None
        cookie = request.cookies.get("coinwatch_session", "")
        user = get_session_user(db, cookie)
        assert user is not None
        require_permission(
            db,
            user,
            "users.manage",
            entity_type="user",
            entity_id=str(user.id),
            request_id=request_id_of(request),
        )
        return JSONResponse(content={"ok": True})

    with TestClient(app) as client:
        with _session() as db:
            viewer = User(
                username="viewer",
                password_hash="not-a-password-hash",
                role="viewer",
                created_at=datetime.now(UTC),
            )
            db.add(viewer)
            db.commit()
            token = create_session(db, viewer)
            db.commit()
        client.cookies.set("coinwatch_session", token)
        response = client.get("/api/probe-deny")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "not_authorized"

    with _session() as db:
        denied = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.denied")).all()
        assert len(denied) == 1
        assert denied[0].result == "denied"


def _session() -> Session:
    """Open a short-lived session on the test database."""
    engine = create_engine()
    return Session(engine)
