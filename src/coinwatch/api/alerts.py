"""Test-alert route.

The handler checks the live session, then calls the alert service. It does
not build a ``signal-cli`` command. The application supplies the sender.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from coinwatch.alerts.base import AlertSender
from coinwatch.api.app import (
    SESSION_COOKIE,
    ApiState,
    error_response,
    request_id_of,
    request_session,
)
from coinwatch.db.models import User
from coinwatch.errors import AlertSendError
from coinwatch.services.alerts import send_test_alert
from coinwatch.services.identity import get_session_user

router = APIRouter()


async def send_test(request: Request) -> Response:
    """Send the operator test alert and return its id and ``sent_at``.

    Requires a live session. ``alerts.manage`` is checked in the service.
    A transport failure is 502 ``alert_failed`` after the error audit row is
    committed, so the request middleware does not drop it.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    try:
        alert = send_test_alert(
            db,
            _sender(request),
            user,
            now=datetime.now(UTC),
            request_id=request_id_of(request),
        )
    except AlertSendError:
        db.commit()
        return error_response(502, "alert_failed", "Alert was not sent.")
    sent_at = alert.sent_at
    if sent_at is None:
        raise RuntimeError("test alert missing sent_at")
    return JSONResponse(content={"id": alert.id, "sent_at": sent_at.isoformat()})


def _session_user(request: Request) -> tuple[Session, User] | JSONResponse:
    """Return the live session user, or 401 when the cookie is missing or dead."""
    db = request_session(request)
    if db is None:
        return error_response(500, "internal", "Internal server error.")
    cookie = request.cookies.get(SESSION_COOKIE)
    user = get_session_user(db, cookie) if cookie else None
    if user is None:
        return error_response(401, "unauthenticated", "Authentication required.")
    return db, user


def _sender(request: Request) -> AlertSender:
    """Return the alert sender stored on the application at startup."""
    api_state = getattr(request.app.state, "api", None)
    if not isinstance(api_state, ApiState):
        raise TypeError("api state is missing")
    return api_state.alert_sender


router.post("/api/alerts/test")(send_test)
