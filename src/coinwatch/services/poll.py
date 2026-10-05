"""Store one coin snapshot from a chain adapter.

The caller owns the database transaction. This module does not import
FastAPI or a chain SDK.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from coinwatch.chains.base import ChainAdapter
from coinwatch.db.models import Coin, Tick


def poll_once(
    session: Session,
    adapter: ChainAdapter,
    chain_id: str,
    coin_address: str,
    *,
    name: str,
    symbol: str,
    now: datetime,
) -> Tick:
    """Upsert the coin and insert one tick. Does not commit.

    The chain row must already exist. An existing coin keeps ``created_at``
    and receives the supplied ``name`` and ``symbol``. ``now`` is the tick
    timestamp and must be timezone-aware. Virtual SOL and token reserves from
    the snapshot are copied onto the tick. The returned tick is pending in
    ``session`` until the caller commits.
    """
    coin = session.get(Coin, (chain_id, coin_address))
    if coin is None:
        coin = Coin(
            chain=chain_id,
            address=coin_address,
            name=name,
            symbol=symbol,
            created_at=now,
            status="active",
        )
        session.add(coin)
        session.flush()
    else:
        coin.name = name
        coin.symbol = symbol
    state = adapter.get_coin_state(coin_address)
    tick = Tick(
        chain=chain_id,
        coin_address=coin_address,
        ts=now,
        price_native=state.price_native,
        price_usd=state.price_usd,
        mcap_usd=state.mcap_usd,
        liquidity_native=state.liquidity_native,
        curve_pct=state.curve_pct,
        volume_1m=state.volume_1m,
        volume_5m=state.volume_5m,
        volume_15m=state.volume_15m,
        holders=state.holders,
        virtual_sol_reserves=state.virtual_sol_reserves,
        virtual_token_reserves=state.virtual_token_reserves,
    )
    session.add(tick)
    session.flush()
    return tick


def is_stale(
    observed_at: datetime | None,
    now: datetime,
    window: timedelta = timedelta(seconds=15),
) -> bool:
    """Return whether a tick is missing or older than ``window``.

    A missing tick (``observed_at is None``) is stale. A tick observed exactly
    ``window`` ago is still fresh. ``observed_at`` and ``now`` are timezone-aware
    instants. The default window is 15 seconds.
    """
    if observed_at is None:
        return True
    return now - observed_at > window
