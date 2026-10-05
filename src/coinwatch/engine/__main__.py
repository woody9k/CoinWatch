"""Engine process entrypoint."""

import logging
import signal
import threading
from types import FrameType

import structlog


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


def main() -> None:
    """Log that the poller is absent and wait until SIGTERM."""
    configure_logging()
    log = structlog.get_logger("coinwatch.engine")
    log.info("poller.not_installed")
    stopped = threading.Event()

    def _stop(_signum: int, _frame: FrameType | None) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, _stop)
    stopped.wait()


if __name__ == "__main__":
    main()
