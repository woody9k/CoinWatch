"""Engine process entrypoint.

When ``SOLANA_RPC_URL`` and ``COINWATCH_SOL_USD`` are both set, poll BobCoin
on the pump.fun bonding curve every 5 seconds, store the tick, record unsent
market-cap crossings, and step running bots for that coin. Otherwise log
``poller.not_installed`` and wait.
The process stops on SIGTERM. Importing this module does not open an RPC
connection.
"""

import logging
import signal
import threading
import time
from datetime import UTC, datetime
from types import FrameType

import structlog
from sqlalchemy.orm import Session, sessionmaker

from coinwatch.chains.base import ChainAdapter
from coinwatch.chains.solana import SolanaPumpAdapter
from coinwatch.db.models import Tick
from coinwatch.db.session import create_engine, session_factory
from coinwatch.errors import ChainReadError
from coinwatch.services.bot_run import step_running_bots
from coinwatch.services.poll import poll_once
from coinwatch.services.price_cross import record_price_alert_crossings
from coinwatch.settings import Settings, get_settings

_CHAIN_ID = "solana"
_BOBCOIN_ADDRESS = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
_BOBCOIN_NAME = "BobCoin"
_BOBCOIN_SYMBOL = "BOB"
_POLL_INTERVAL_SECONDS = 5.0


def configure_logging() -> None:
    """Emit JSON logs on stdout."""
    logging.basicConfig(format="%(message)s", level=logging.INFO)
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


def poller_configured(settings: Settings) -> bool:
    """Return whether the engine should poll BobCoin.

    Both ``SOLANA_RPC_URL`` and ``COINWATCH_SOL_USD`` must be set. An empty
    SOL price is not replaced with a guessed rate.
    """
    return settings.solana_rpc_url != "" and settings.sol_usd is not None


def main() -> None:
    """Poll BobCoin until SIGTERM, or wait when the poller is not configured."""
    configure_logging()
    log = structlog.get_logger("coinwatch.engine")
    settings = get_settings()
    stopped = threading.Event()

    def _stop(_signum: int, _frame: FrameType | None) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, _stop)
    sol_usd = settings.sol_usd
    if not poller_configured(settings) or sol_usd is None:
        log.info("poller.not_installed")
        stopped.wait()
        return
    log.info("poller.started", chain=_CHAIN_ID, mint=_BOBCOIN_ADDRESS)
    adapter = SolanaPumpAdapter(settings.solana_rpc_url, sol_usd)
    _poll_until_stopped(stopped, adapter)


def _poll_until_stopped(stopped: threading.Event, adapter: ChainAdapter) -> None:
    """Read BobCoin every 5 seconds until ``stopped`` is set.

    Each pass stores a tick, records market-cap crossings, and then steps
    running bots. ``ChainReadError`` is logged with the mint and failure
    label, then the loop continues without recording crossings or stepping
    bots. A crossing scan failure or a bot step failure does not stop the
    loop. The RPC URL is not logged.
    """
    factory = session_factory(create_engine())
    while not stopped.is_set():
        started = time.monotonic()
        poll_and_step(
            factory,
            adapter,
            _CHAIN_ID,
            _BOBCOIN_ADDRESS,
            name=_BOBCOIN_NAME,
            symbol=_BOBCOIN_SYMBOL,
            now=datetime.now(UTC),
        )
        remaining = _POLL_INTERVAL_SECONDS - (time.monotonic() - started)
        remaining = max(remaining, 0)
        if stopped.wait(remaining):
            return


def poll_and_step(
    factory: sessionmaker[Session],
    adapter: ChainAdapter,
    chain: str,
    coin_address: str,
    *,
    name: str,
    symbol: str,
    now: datetime,
) -> None:
    """Store one tick, record crossings, then step running bots for that coin.

    The tick is committed before crossings are scanned and before any bot
    runs. ``ChainReadError`` is logged as ``poll.failed`` and returns without
    scanning or stepping, because there is no new tick. ``now`` is a
    timezone-aware timestamp stored on the tick and passed to the bots. A
    failure while scanning crossings or while stepping is logged and does not
    propagate. The RPC URL is not logged.
    """
    log = structlog.get_logger("coinwatch.engine")
    tick_id: int | None = None
    try:
        with factory() as session:
            tick = poll_once(
                session,
                adapter,
                chain,
                coin_address,
                name=name,
                symbol=symbol,
                now=now,
            )
            session.commit()
            tick_id = tick.id
    except ChainReadError as exc:
        log.warning("poll.failed", mint=exc.mint, failure=exc.failure)
        return
    log.info("poll.tick", mint=coin_address, chain=chain)
    if tick_id is not None:
        _scan_price_alerts(factory, chain=chain, coin_address=coin_address, tick_id=tick_id)
    _step_after_poll(factory, chain=chain, coin_address=coin_address, now=now)


def _scan_price_alerts(
    factory: sessionmaker[Session],
    *,
    chain: str,
    coin_address: str,
    tick_id: int,
) -> None:
    """Open a new session and record unsent crossings for a committed tick.

    Called only after the poll transaction has committed, in the same place
    running bots are stepped. The scan does not send an alert. An unexpected
    failure rolls this session back, is logged with the exception type name,
    and does not propagate, so the bot step still runs. The exception message
    is not logged.
    """
    log = structlog.get_logger("coinwatch.engine")
    try:
        with factory() as session:
            tick = session.get(Tick, tick_id)
            if tick is None:
                return
            record_price_alert_crossings(session, chain, coin_address, tick)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("price_alert.cross", exc_type=type(exc).__name__)


def _step_after_poll(
    factory: sessionmaker[Session],
    *,
    chain: str,
    coin_address: str,
    now: datetime,
) -> None:
    """Open a new session and step running bots for a committed tick.

    Called only after the poll transaction has committed. Each returned
    decision is logged as ``bot.step`` with the bot id and outcome. YAML and
    amounts are not logged. An unexpected failure is logged with the
    exception type name and does not propagate.
    """
    log = structlog.get_logger("coinwatch.engine")
    try:
        with factory() as session:
            decisions = step_running_bots(session, chain, coin_address, now=now)
            for decision in decisions:
                log.info("bot.step", bot_id=decision.bot_id, outcome=decision.outcome)
    except Exception as exc:  # noqa: BLE001
        log.warning("bot.step", exc_type=type(exc).__name__)


if __name__ == "__main__":
    main()
