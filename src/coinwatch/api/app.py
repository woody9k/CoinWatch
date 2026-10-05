"""FastAPI application: session cookie, request guard, and error shape.

Routes call identity and audit services. They do not contain trading rules.
One database session is opened per request and closed when the response is
finished. Successful requests commit. A permission denial is committed before
the 403 is returned so the flushed ``auth.denied`` row is kept.
"""

import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException

from coinwatch.alerts.base import AlertSender
from coinwatch.alerts.signal_cli import SignalCliSender
from coinwatch.authz import permissions_for
from coinwatch.db.models import User, UserSession
from coinwatch.db.session import create_engine, session_factory
from coinwatch.errors import NotAuthorized
from coinwatch.services.audit import record_audit
from coinwatch.services.identity import (
    authenticate,
    create_session,
    ensure_admin,
    get_session_user,
)
from coinwatch.settings import get_settings

SESSION_COOKIE = "coinwatch_session"
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_RequestHandler = Callable[[Request], Awaitable[Response]]


class ApiState:
    """Process state shared by requests after startup."""

    def __init__(self, sessions: sessionmaker[Session], alert_sender: AlertSender) -> None:
        """Keep the session factory and the alert sender for this process."""
        self.sessions = sessions
        self.alert_sender = alert_sender


class LoginRequest(BaseModel):
    """Credentials for ``POST /api/login``. The password is never logged."""

    username: str
    password: SecretStr


class LoginResponse(BaseModel):
    """Identity returned after a session cookie is set."""

    username: str
    role: str


class MeResponse(BaseModel):
    """Signed-in user and the permissions granted by that user's role."""

    username: str
    role: str
    permissions: list[str]


class LogoutResponse(BaseModel):
    """Acknowledgement that logout finished."""

    ok: bool


def configure_logging() -> None:
    """Emit JSON logs with ``ts``, ``level``, ``logger``, and ``event``."""
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    api_logger = logging.getLogger("coinwatch.api")
    api_logger.setLevel(logging.INFO)
    api_logger.disabled = False
    structlog.configure(
        processors=[
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", key="ts"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Return the stable CoinWatch error body."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def request_session(request: Request) -> Session | None:
    """Return the database session opened for ``request``, if one exists."""
    db = getattr(request.state, "db", None)
    if isinstance(db, Session):
        return db
    return None


def request_id_of(request: Request) -> str:
    """Return the request id assigned at the edge of this request."""
    request_id = getattr(request.state, "request_id", "")
    if isinstance(request_id, str):
        return request_id
    return ""


def create_app(alert_sender: AlertSender | None = None) -> FastAPI:
    """Build the CoinWatch HTTP application.

    Startup seeds the bootstrap admin and commits that insert. The process
    listens only when ``main`` runs it; this function does not bind a port.
    When ``alert_sender`` is omitted, the app uses ``SignalCliSender`` built
    from settings. Tests pass a fake sender.
    """
    configure_logging()
    log = structlog.get_logger("coinwatch.api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Open the database, seed the admin, and close the engine on shutdown."""
        engine = create_engine()
        factory = session_factory(engine)
        app.state.api = ApiState(factory, _alert_sender(alert_sender))
        with factory() as session:
            ensure_admin(session)
            session.commit()
        yield
        engine.dispose()

    app = FastAPI(title="CoinWatch", lifespan=lifespan)

    @app.exception_handler(NotAuthorized)
    async def not_authorized(request: Request, exc: NotAuthorized) -> JSONResponse:
        """Commit a flushed denial audit, then return 403.

        ``require_permission`` flushes ``auth.denied`` and does not commit.
        Committing here keeps that row. The permission name stays in the audit
        row; the response message does not echo the exception.
        """
        del exc
        db = request_session(request)
        if db is not None:
            db.commit()
            request.state.denial_committed = True
        return error_response(403, "not_authorized", "Not authorized.")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Return a generic validation error without echoing the body."""
        del request, exc
        return error_response(422, "invalid_request", "Invalid request.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        """Map framework HTTP errors onto the CoinWatch error body."""
        del request
        if exc.status_code == 404:
            return error_response(404, "not_found", "Not found.")
        return error_response(exc.status_code, "http_error", "Request failed.")

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        """Return a 500 whose message cannot include a password or traceback."""
        del exc
        db = request_session(request)
        if db is not None:
            db.rollback()
        return error_response(500, "internal", "Internal server error.")

    @app.middleware("http")
    async def bind_request(request: Request, call_next: _RequestHandler) -> Response:
        """Assign a request id, guard mutations, and close the database session."""
        request_id = _incoming_request_id(request)
        request.state.request_id = request_id
        if _missing_request_header(request):
            response = error_response(
                403,
                "missing_request_header",
                "Missing X-CoinWatch-Request header.",
            )
            _log_request(log, request, response.status_code)
            return response
        api_state = getattr(request.app.state, "api", None)
        if not isinstance(api_state, ApiState):
            response = error_response(500, "internal", "Internal server error.")
            _log_request(log, request, response.status_code)
            return response
        db = api_state.sessions()
        request.state.db = db
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            if status_code < 400 or _denial_was_committed(request):
                db.commit()
            else:
                db.rollback()
            return response
        except Exception:
            status_code = 500
            db.rollback()
            raise
        finally:
            _log_request(log, request, status_code)
            db.close()

    @app.get("/api/status")
    async def status() -> dict[str, str]:
        """Report that the API process is up. This route does not require a session."""
        return {"status": "ok", "service": "coinwatch"}

    @app.post("/api/login")
    async def login(request: Request, body: LoginRequest) -> Response:
        """Check the password, set the session cookie, and audit the attempt."""
        db = _require_session(request)
        user = authenticate(db, body.username, body.password.get_secret_value())
        if user is None:
            _audit_login_failure(db, request, body.username)
            db.commit()
            return error_response(401, "invalid_login", "Invalid username or password.")
        session_id = create_session(db, user)
        record_audit(
            db,
            actor_type="user",
            actor_id=str(user.id),
            action="auth.login",
            entity_type="user",
            entity_id=str(user.id),
            result="ok",
            request_id=request_id_of(request),
        )
        response = JSONResponse(
            content=LoginResponse(username=user.username, role=user.role).model_dump()
        )
        response.set_cookie(
            key=SESSION_COOKIE,
            value=session_id,
            httponly=True,
            samesite="lax",
            path="/",
            secure=False,
        )
        return response

    @app.post("/api/logout")
    async def logout(request: Request) -> Response:
        """Clear the session cookie and audit logout when a live session exists."""
        db = _require_session(request)
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie:
            user = get_session_user(db, cookie)
            row = db.get(UserSession, cookie)
            if user is not None and row is not None:
                db.delete(row)
                record_audit(
                    db,
                    actor_type="user",
                    actor_id=str(user.id),
                    action="auth.logout",
                    entity_type="user",
                    entity_id=str(user.id),
                    result="ok",
                    request_id=request_id_of(request),
                )
        response = JSONResponse(content=LogoutResponse(ok=True).model_dump())
        response.delete_cookie(key=SESSION_COOKIE, path="/", samesite="lax")
        return response

    @app.get("/api/me")
    async def me(request: Request) -> Response:
        """Return the session user and the permissions for that user's role."""
        db = _require_session(request)
        cookie = request.cookies.get(SESSION_COOKIE)
        user = get_session_user(db, cookie) if cookie else None
        if user is None:
            return error_response(401, "unauthenticated", "Authentication required.")
        payload = MeResponse(
            username=user.username,
            role=user.role,
            permissions=sorted(permissions_for(user.role)),
        )
        return JSONResponse(content=payload.model_dump())

    # Import after this module defines the helpers the router calls.
    from coinwatch.api.alerts import router as alerts_router
    from coinwatch.api.controls import router as controls_router
    from coinwatch.api.reads import router as reads_router

    app.include_router(reads_router)
    app.include_router(alerts_router)
    app.include_router(controls_router)
    return app


def _alert_sender(alert_sender: AlertSender | None) -> AlertSender:
    """Return the injected sender, or a ``SignalCliSender`` from settings."""
    if alert_sender is not None:
        return alert_sender
    settings = get_settings()
    return SignalCliSender(settings.signal_cli_bin, settings.signal_account)


def _require_session(request: Request) -> Session:
    """Return the request session or raise when middleware did not open one."""
    db = request_session(request)
    if db is None:
        raise RuntimeError("database session is missing")
    return db


def _audit_login_failure(db: Session, request: Request, username: str) -> None:
    """Record ``auth.login`` denied. The password is not stored."""
    existing = db.scalar(select(User).where(User.username == username))
    actor_id = str(existing.id) if existing is not None else ""
    entity_id = str(existing.id) if existing is not None else username[:128]
    record_audit(
        db,
        actor_type="user",
        actor_id=actor_id,
        action="auth.login",
        entity_type="user",
        entity_id=entity_id,
        result="denied",
        request_id=request_id_of(request),
    )


def _incoming_request_id(request: Request) -> str:
    """Use a safe ``X-Request-Id`` or allocate a new id."""
    incoming = request.headers.get("x-request-id", "")
    if _REQUEST_ID_PATTERN.fullmatch(incoming):
        return incoming
    return str(uuid.uuid4())


def _missing_request_header(request: Request) -> bool:
    """Return whether a mutating method omitted the CoinWatch request header."""
    if request.method not in _MUTATING_METHODS:
        return False
    return request.headers.get("x-coinwatch-request") != "1"


def _denial_was_committed(request: Request) -> bool:
    """Return whether the not-authorized handler already committed this request."""
    return getattr(request.state, "denial_committed", False) is True


def _log_request(log: structlog.stdlib.BoundLogger, request: Request, status_code: int) -> None:
    """Write one ``http.request`` line. The body, including any password, is omitted."""
    log.info(
        "http.request",
        request_id=request_id_of(request),
        method=request.method,
        path=request.url.path,
        status=status_code,
    )
