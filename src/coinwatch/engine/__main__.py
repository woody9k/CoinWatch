"""Engine process entrypoint.

When ``SOLANA_RPC_URL`` and ``COINWATCH_SOL_USD`` are both set, poll BobCoin
on the pump.fun bonding curve every 5 seconds and store ticks. Otherwise log
``poller.not_installed`` and wait. The process stops on SIGTERM. Importing
this module does not open an RPC connection.
"""

import logging
import signal
import threading
import time
from datetime import UTC, datetime
from types import FrameType

import structlog

from coinwatch.chains.base import ChainAdapter
from coinwatch.chains.solana import SolanaPumpAdapter
from coinwatch.db.session import create_engine, session_factory
from coinwatch.errors import ChainReadError
from coinwatch.services.poll import poll_once
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

    ``ChainReadError`` is logged with the mint and failure label, then the
    loop continues. The RPC URL is not logged.
    """
    log = structlog.get_logger("coinwatch.engine")
    factory = session_factory(create_engine())
    while not stopped.is_set():
        started = time.monotonic()
        try:
            with factory() as session:
                poll_once(
                    session,
                    adapter,
                    _CHAIN_ID,
                    _BOBCOIN_ADDRESS,
                    name=_BOBCOIN_NAME,
                    symbol=_BOBCOIN_SYMBOL,
                    now=datetime.now(UTC),
                )
                session.commit()
        except ChainReadError as exc:
            log.warning("poll.failed", mint=exc.mint, failure=exc.failure)
        else:
            log.info("poll.tick", mint=_BOBCOIN_ADDRESS, chain=_CHAIN_ID)
        remaining = _POLL_INTERVAL_SECONDS - (time.monotonic() - started)
        remaining = max(remaining, 0)
        if stopped.wait(remaining):
            return


if __name__ == "__main__":
    main()
