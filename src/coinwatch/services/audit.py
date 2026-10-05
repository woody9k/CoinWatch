"""Append-only audit inserts.

``record_audit`` adds one ``audit_events`` row to the caller's transaction.
It does not commit. Application code does not update or delete audit rows.
``before`` and ``after`` are field snapshots. Do not put passwords, seeds,
private keys, or API keys in them.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from coinwatch.db.models import AuditEvent

_SECRET_MARKERS: tuple[str, ...] = (
    "password",
    "secret",
    "seed",
    "private_key",
    "api_key",
    "passphrase",
)


def record_audit(
    session: Session,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    result: str,
    before: Mapping[str, object] | None = None,
    after: Mapping[str, object] | None = None,
    request_id: str = "",
    detail: str = "",
    ts: datetime | None = None,
) -> AuditEvent:
    """Insert an audit row and flush it. The caller commits.

    ``result`` is ``ok``, ``denied``, or ``error``. ``before`` and ``after``
    are copied and secret-named keys are dropped. This function does not commit.
    """
    event = AuditEvent(
        ts=datetime.now(UTC) if ts is None else ts,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        result=result,
        before_json=_snapshot(before),
        after_json=_snapshot(after),
        request_id=request_id,
        detail=detail,
    )
    session.add(event)
    session.flush()
    return event


def _snapshot(payload: Mapping[str, object] | None) -> dict[str, object]:
    """Copy a JSON snapshot, omitting secret-named keys."""
    if payload is None:
        return {}
    return _redact(payload)


def _redact(payload: Mapping[str, object]) -> dict[str, object]:
    """Drop keys whose names look like credentials."""
    clean: dict[str, object] = {}
    for key, value in payload.items():
        if _is_secret_key(key):
            continue
        if isinstance(value, dict):
            nested = {str(item_key): item_value for item_key, item_value in value.items()}
            clean[key] = _redact(nested)
        else:
            clean[key] = value
    return clean


def _is_secret_key(key: str) -> bool:
    """Return whether ``key`` names a secret field."""
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)
