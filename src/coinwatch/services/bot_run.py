"""Step every running bot for one coin after a tick is stored.

The caller owns the session and must commit its own work first. A commit
inside this function would otherwise include those pending rows, and a
rollback would drop them. This module does not open an engine, call an RPC,
or send a transaction.
"""

from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.db.models import Bot, Decision
from coinwatch.services.bot_step import step_bot


def step_running_bots(
    session: Session,
    chain: str,
    coin_address: str,
    *,
    now: datetime,
) -> list[Decision]:
    """Step each running bot for ``chain`` and ``coin_address``.

    Bots are loaded in id order. ``now`` is a timezone-aware timestamp passed
    to each step. After a step returns, this function commits that bot's
    decision and any paper fill. It does not commit before the first bot, so
    pending rows the caller has not committed stay pending when no bot runs
    or every step fails. An exception rolls the session back, is logged as
    ``bot.step`` with the bot id and the exception type name, and does not
    stop the remaining bots. The log has no exception message. Decisions lost
    to a rollback are omitted from the result.
    """
    bots = session.scalars(
        select(Bot)
        .where(Bot.chain == chain, Bot.coin_address == coin_address, Bot.status == "running")
        .order_by(Bot.id)
    ).all()
    decisions: list[Decision] = []
    for bot in bots:
        decision = _commit_step(session, bot, now)
        if decision is not None:
            decisions.append(decision)
    return decisions


def _commit_step(session: Session, bot: Bot, now: datetime) -> Decision | None:
    """Commit one bot step. On failure, roll back and return ``None``.

    The warning names the exception type and the bot id. It does not include
    the exception message, so a wallet key in that message cannot reach the log.
    Any exception is caught so the caller can step the next bot.
    """
    bot_id = bot.id
    try:
        decision = step_bot(session, bot, now=now)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        structlog.get_logger("coinwatch.services").warning(
            "bot.step",
            bot_id=bot_id,
            exc_type=type(exc).__name__,
        )
        return None
    return decision
