"""Step one paper bot from its strategy and the latest ticks.

Holder and dev scans are not wired, so ``dev_sold`` on the snapshot is always
``None``. This module does not commit, call an RPC, or send a transaction.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.db.models import Bot, Coin, Decision, Position, Strategy, Tick
from coinwatch.errors import KillSwitchEngaged, QuoteRejected, StaleQuote, StrategyError
from coinwatch.quoting import Quote, quote_buy, quote_sell
from coinwatch.services.paper import apply_paper_fill
from coinwatch.settings import get_settings
from coinwatch.strategy import FiredRule, Snapshot, evaluate, load_strategy

_FIVE_MINUTES = timedelta(minutes=5)
_HUNDRED = Decimal(100)
_ZERO = Decimal(0)
_SLIPPAGE_PCT = Decimal(10)
_MAX_IMPACT_PCT = Decimal(10)


def step_bot(session: Session, bot: Bot, *, now: datetime) -> Decision | None:
    """Evaluate one bot and maybe fill a paper trade. Does not commit.

    A status other than ``running`` returns ``None`` and writes nothing.
    ``now`` is a timezone-aware timestamp stored on the decision. A paper buy
    uses the configured paper SOL balance as the wallet. Holder and dev scans
    are not wired, so ``dev_sold`` is always missing and a rule that watches
    it does not fire.
    """
    if bot.status != "running":
        return None
    if not bot.paper:
        return _decision(
            session,
            bot,
            now,
            rule="",
            outcome="live_disabled",
            detail="live sends are not implemented",
        )
    strategy_row = session.get(Strategy, bot.strategy_id)
    if strategy_row is None:
        return _decision(
            session,
            bot,
            now,
            rule="",
            outcome="rejected",
            detail="strategy is missing",
        )
    try:
        strategy = load_strategy(strategy_row.yaml_config)
    except StrategyError as exc:
        return _decision(session, bot, now, rule="", outcome="rejected", detail=str(exc))

    latest = _latest_tick(session, bot)
    if latest is None:
        return _decision(session, bot, now, rule="", outcome="rejected", detail="no tick")

    snapshot = _snapshot(session, bot, latest, now)
    evaluation = evaluate(strategy, snapshot, _armed(bot.armed_rules))
    bot.armed_rules = json.dumps(sorted(evaluation.armed))
    winner = evaluation.winner
    if winner is None:
        return _decision(session, bot, now, rule="", outcome="quiet", detail="")

    field = strategy.rules[winner.index].field
    try:
        quote = _quote(session, bot, latest, winner, now=now)
    except (QuoteRejected, StaleQuote, KillSwitchEngaged) as exc:
        return _decision(session, bot, now, rule=field, outcome="rejected", detail=exc.code)
    apply_paper_fill(session, bot, quote, now=now)
    return _decision(session, bot, now, rule=field, outcome="filled", detail=winner.action)


def _latest_tick(session: Session, bot: Bot) -> Tick | None:
    """Return the newest tick for the bot's coin."""
    return session.scalar(
        select(Tick)
        .where(Tick.chain == bot.chain, Tick.coin_address == bot.coin_address)
        .order_by(Tick.ts.desc(), Tick.id.desc())
        .limit(1)
    )


def _snapshot(session: Session, bot: Bot, latest: Tick, now: datetime) -> Snapshot:
    """Read market cap and curve progress from ``latest``.

    ``price_change_pct_5m`` compares ``latest`` with the newest tick at or
    before five minutes ago. Holder and dev scans are not wired, so
    ``dev_sold`` is always ``None``.
    """
    cutoff = now - _FIVE_MINUTES
    then = session.scalar(
        select(Tick)
        .where(
            Tick.chain == bot.chain,
            Tick.coin_address == bot.coin_address,
            Tick.ts <= cutoff,
        )
        .order_by(Tick.ts.desc(), Tick.id.desc())
        .limit(1)
    )
    return Snapshot(
        mcap_usd=latest.mcap_usd,
        price_change_pct_5m=_price_change(latest, then),
        curve_pct=latest.curve_pct,
        dev_sold=None,
    )


def _price_change(latest: Tick, then: Tick | None) -> Decimal | None:
    """Return the percent move from ``then`` to ``latest``.

    Missing history and a zero starting price leave the field unset.
    """
    if then is None or then.price_native == _ZERO:
        return None
    return (latest.price_native - then.price_native) / then.price_native * _HUNDRED


def _armed(raw: str | None) -> frozenset[int]:
    """Read armed rule indexes. Null and bad JSON are an empty set."""
    if raw is None:
        return frozenset()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return frozenset()
    if not isinstance(parsed, list):
        return frozenset()
    indexes: list[int] = []
    for item in parsed:
        if isinstance(item, bool) or not isinstance(item, int):
            return frozenset()
        indexes.append(item)
    return frozenset(indexes)


def _quote(session: Session, bot: Bot, latest: Tick, winner: FiredRule, *, now: datetime) -> Quote:
    """Quote the winning action. A sell with no size is rejected before quoting."""
    settings = get_settings()
    coin = session.get(Coin, (bot.chain, bot.coin_address))
    complete = coin is not None and (coin.status == "graduated" or coin.graduated_at is not None)
    if winner.action == "buy":
        amount = winner.amount_native
        if amount is None:
            raise QuoteRejected("unfilled", "buy amount is missing")
        return quote_buy(
            amount,
            virtual_sol=latest.virtual_sol_reserves,
            virtual_token=latest.virtual_token_reserves,
            observed_at=latest.ts,
            now=now,
            complete=complete,
            wallet_balance_sol=settings.paper_balance_sol,
            kill_switch=settings.coinwatch_kill_switch,
            slippage_pct=_SLIPPAGE_PCT,
            max_impact_pct=_MAX_IMPACT_PCT,
        )
    position = session.get(Position, bot.id)
    if position is None or position.size <= _ZERO:
        raise QuoteRejected("position", "position size is zero")
    if winner.action == "sell_pct":
        pct = winner.pct
        if pct is None:
            raise QuoteRejected("unfilled", "sell percent is missing")
        token_in = position.size * pct / _HUNDRED
    else:
        token_in = position.size
    return quote_sell(
        token_in,
        virtual_sol=latest.virtual_sol_reserves,
        virtual_token=latest.virtual_token_reserves,
        observed_at=latest.ts,
        now=now,
        complete=complete,
        wallet_balance_sol=settings.paper_balance_sol,
        position_size=position.size,
        kill_switch=settings.coinwatch_kill_switch,
        slippage_pct=_SLIPPAGE_PCT,
        max_impact_pct=_MAX_IMPACT_PCT,
    )


def _decision(
    session: Session,
    bot: Bot,
    now: datetime,
    *,
    rule: str,
    outcome: str,
    detail: str,
) -> Decision:
    """Insert one decision row and flush it. Does not commit."""
    row = Decision(bot_id=bot.id, ts=now, rule=rule, outcome=outcome, detail=detail)
    session.add(row)
    session.flush()
    return row
