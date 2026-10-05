"""Record an unsent alert when a market-cap threshold is crossed.

The caller owns the database transaction. This module does not commit, send
a message, or import an alert sender.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.db.models import Alert, PriceAlert, Tick
from coinwatch.services.audit import record_audit

_ABOVE = "mcap_usd_above"
_BELOW = "mcap_usd_below"
_ACTOR_TYPE = "system"
_ACTOR_ID = "system"


def record_price_alert_crossings(
    session: Session,
    chain: str,
    coin_address: str,
    tick: Tick,
) -> list[Alert]:
    """Insert one unsent alert for each enabled market-cap alert that crosses.

    ``chain`` and ``coin_address`` identify the coin. ``tick`` is the new
    poll for that coin. Enabled ``price_alerts`` on the coin are loaded and
    compared with ``tick.mcap_usd``. ``mcap_usd_above`` is true when market
    cap is greater than the threshold. ``mcap_usd_below`` is true when market
    cap is less than the threshold. Any other condition is ignored. A value
    equal to the threshold is not a cross.

    The previous tick is the newest row for the same coin with ``ts`` strictly
    before ``tick.ts``. When several older ticks share that timestamp, the
    greatest ``id`` wins. If there is no previous tick, nothing is recorded.
    A row is inserted only when the comparison is true on ``tick`` and was not
    true on the previous tick.

    Each insert is an ``alerts`` row with type ``price_alert``, ``sent_at``
    null, and ``escalated`` false. The message is one line: the condition,
    the threshold, and ``mcap_usd``, each decimal written with ``format``.
    The same transaction appends an audit row with action
    ``price_alert.cross``, result ``ok``, entity type ``price_alert``, and
    the price alert id. The actor is the system user. This function does not
    send the alert and does not commit.
    """
    previous = _previous_tick(session, chain, coin_address, tick)
    if previous is None:
        return []
    alerts = session.scalars(
        select(PriceAlert)
        .where(
            PriceAlert.chain == chain,
            PriceAlert.coin_address == coin_address,
            PriceAlert.enabled.is_(True),
        )
        .order_by(PriceAlert.id)
    ).all()
    recorded: list[Alert] = []
    for price_alert in alerts:
        crossed = _crossed(
            price_alert.condition,
            previous.mcap_usd,
            tick.mcap_usd,
            price_alert.threshold,
        )
        if not crossed:
            continue
        recorded.append(_record(session, price_alert, tick))
    return recorded


def _previous_tick(session: Session, chain: str, coin_address: str, tick: Tick) -> Tick | None:
    """Return the newest earlier tick for this coin, or ``None``.

    Earlier means ``ts`` strictly before ``tick.ts``. The greatest ``id``
    breaks a timestamp tie.
    """
    return session.scalar(
        select(Tick)
        .where(
            Tick.chain == chain,
            Tick.coin_address == coin_address,
            Tick.ts < tick.ts,
        )
        .order_by(Tick.ts.desc(), Tick.id.desc())
        .limit(1)
    )


def _crossed(condition: str, previous: Decimal, current: Decimal, threshold: Decimal) -> bool:
    """Return whether ``condition`` became true between the two market caps."""
    if condition == _ABOVE:
        return current > threshold and not (previous > threshold)
    if condition == _BELOW:
        return current < threshold and not (previous < threshold)
    return False


def _record(session: Session, price_alert: PriceAlert, tick: Tick) -> Alert:
    """Insert the unsent alert and its audit row. Does not commit."""
    threshold = format(price_alert.threshold, "f")
    mcap_usd = format(tick.mcap_usd, "f")
    alert = Alert(
        chain=price_alert.chain,
        coin_address=price_alert.coin_address,
        type="price_alert",
        message=f"{price_alert.condition} {threshold} {mcap_usd}",
        sent_at=None,
        escalated=False,
    )
    session.add(alert)
    record_audit(
        session,
        actor_type=_ACTOR_TYPE,
        actor_id=_ACTOR_ID,
        action="price_alert.cross",
        entity_type="price_alert",
        entity_id=str(price_alert.id),
        result="ok",
        after={"mcap_usd": mcap_usd},
    )
    return alert
