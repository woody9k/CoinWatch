"""Apply a paper fill from a quote. Nothing is sent to the chain.

The caller owns the transaction. This module inserts the trade, updates the
position, and appends the audit row, then returns. It does not commit.
Live bots are refused because live sends are not implemented.
"""

from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.db.models import Bot, Position, Tick, Trade
from coinwatch.errors import QuoteRejected
from coinwatch.quoting import Quote
from coinwatch.services.audit import record_audit

_MONEY = Decimal("0.000000000000000001")
_ZERO = Decimal(0)


def apply_paper_fill(
    session: Session,
    bot: Bot,
    quote: Quote,
    *,
    now: datetime,
    actor_id: str = "system",
) -> Trade:
    """Insert a paper trade and update the bot position. Does not commit.

    Buy increases ``size`` by ``expected_out`` and ``cost_native`` by the SOL
    spent. Sell decreases ``size`` by the tokens sold and adds
    ``sol_out - cost_removed`` to ``realized_pnl_native``, where
    ``cost_removed`` is ``cost_native * (token_in / size_before)``. A missing
    position is created on a buy. ``now`` is a timezone-aware timestamp.
    ``price_usd`` and ``mcap_usd`` are copied from the latest tick when one
    exists. The audit action is ``trade.submit``. Raises ``QuoteRejected``
    with code ``live_disabled`` when ``bot.paper`` is false, and with code
    ``position`` when a sell is larger than the open size.
    """
    if not bot.paper:
        raise QuoteRejected("live_disabled", "live sends are not implemented")
    position = _position_for_fill(session, bot, quote, now)
    _apply_quote(position, quote, now)
    price_usd, mcap_usd = _latest_marks(session, bot)
    trade = Trade(
        bot_id=bot.id,
        chain=bot.chain,
        coin_address=bot.coin_address,
        side=quote.side,
        amount_native=quote.sol_debited if quote.side == "buy" else quote.sol_credited,
        price_native=_fill_price(quote),
        price_usd=price_usd,
        mcap_usd=mcap_usd,
        fee_native=quote.fee_native,
        price_impact_pct=quote.price_impact_pct,
        tx_sig=None,
        paper=True,
        actor_id=actor_id,
        ts=now,
    )
    session.add(trade)
    session.flush()
    record_audit(
        session,
        actor_type="system" if actor_id == "system" else "user",
        actor_id=actor_id,
        action="trade.submit",
        entity_type="trade",
        entity_id=str(trade.id),
        result="ok",
        before={},
        after={
            "side": quote.side,
            "paper": True,
            "size": format(position.size, "f"),
            "cost_native": format(position.cost_native, "f"),
            "realized_pnl_native": format(position.realized_pnl_native, "f"),
        },
        ts=now,
        detail=quote.side,
    )
    return trade


def _position_for_fill(session: Session, bot: Bot, quote: Quote, now: datetime) -> Position:
    """Return the bot position, creating an empty one for a buy."""
    position = session.get(Position, bot.id)
    if quote.side == "sell" and (
        position is None or position.size <= _ZERO or quote.amount_in > position.size
    ):
        raise QuoteRejected("position", "sell size is greater than the position")
    if position is None:
        position = Position(
            bot_id=bot.id,
            chain=bot.chain,
            coin_address=bot.coin_address,
            size=_ZERO,
            cost_native=_ZERO,
            realized_pnl_native=_ZERO,
            updated_at=now,
        )
        session.add(position)
    return position


def _apply_quote(position: Position, quote: Quote, now: datetime) -> None:
    """Move size, cost, and realized P&L from ``quote`` onto ``position``."""
    if quote.side == "buy":
        position.size += quote.expected_out
        position.cost_native += quote.sol_debited
    else:
        size_before = position.size
        cost_removed = position.cost_native * (quote.amount_in / size_before)
        position.size = size_before - quote.amount_in
        position.cost_native = position.cost_native - cost_removed
        position.realized_pnl_native += quote.sol_credited - cost_removed
    position.updated_at = now


def _latest_marks(session: Session, bot: Bot) -> tuple[Decimal, Decimal]:
    """Return USD price and market cap from the latest tick, or zeros."""
    tick = session.scalar(
        select(Tick)
        .where(Tick.chain == bot.chain, Tick.coin_address == bot.coin_address)
        .order_by(Tick.ts.desc())
        .limit(1)
    )
    if tick is None:
        return _ZERO, _ZERO
    return tick.price_usd, tick.mcap_usd


def _fill_price(quote: Quote) -> Decimal:
    """Return SOL per token for the fill, quantized down to 18 places."""
    if quote.side == "buy":
        raw = quote.sol_debited / quote.expected_out
    else:
        raw = quote.sol_credited / quote.amount_in
    return raw.quantize(_MONEY, rounding=ROUND_DOWN)
