"""Operator test alerts.

The caller owns the transaction. ``send_test_alert`` inserts the alert and
the audit row, then returns. It does not commit.
"""

from datetime import datetime

import structlog
from sqlalchemy.orm import Session

from coinwatch.alerts.base import AlertSender
from coinwatch.db.models import Alert, User
from coinwatch.errors import AlertSendError
from coinwatch.services.audit import record_audit
from coinwatch.services.identity import require_permission
from coinwatch.settings import get_settings

TEST_ALERT_MESSAGE = "CoinWatch test alert"
_TEST_CHAIN = "solana"


def send_test_alert(
    session: Session,
    sender: AlertSender,
    actor: User,
    *,
    now: datetime,
    request_id: str,
) -> Alert:
    """Insert a test alert, send it, and audit the result. Does not commit.

    Requires ``alerts.manage``. The row uses type ``test``, chain ``solana``,
    a null coin address, and ``escalated`` false. The recipient comes from
    settings. On success ``sent_at`` is ``now`` and the audit result is
    ``ok``. On ``AlertSendError``, ``sent_at`` stays null, the audit result
    is ``error``, and the error is raised again. The log line is
    ``alert.send`` plus the result. The message body is not logged.
    """
    require_permission(
        session,
        actor,
        "alerts.manage",
        entity_type="alert",
        entity_id="test",
        request_id=request_id,
    )
    alert = Alert(
        chain=_TEST_CHAIN,
        coin_address=None,
        type="test",
        message=TEST_ALERT_MESSAGE,
        sent_at=None,
        escalated=False,
    )
    session.add(alert)
    session.flush()
    recipient = get_settings().signal_recipient
    try:
        sender.send(recipient, TEST_ALERT_MESSAGE)
    except AlertSendError:
        record_audit(
            session,
            actor_type="user",
            actor_id=str(actor.id),
            action="alert.send",
            entity_type="alert",
            entity_id=str(alert.id),
            result="error",
            request_id=request_id,
        )
        _log_send(result="error", request_id=request_id)
        raise
    alert.sent_at = now
    record_audit(
        session,
        actor_type="user",
        actor_id=str(actor.id),
        action="alert.send",
        entity_type="alert",
        entity_id=str(alert.id),
        result="ok",
        request_id=request_id,
    )
    _log_send(result="ok", request_id=request_id)
    return alert


def _log_send(*, result: str, request_id: str) -> None:
    """Write ``alert.send`` with the result. The message body is omitted."""
    structlog.get_logger("coinwatch.alerts").info(
        "alert.send",
        result=result,
        request_id=request_id,
    )
