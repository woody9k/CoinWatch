"""Typed domain errors shared by services, the API, and the engine.

The API maps these to HTTP. The engine maps them to a decision row and a log
line. Messages can change; callers should branch on the exception type.
"""


class CoinWatchError(Exception):
    """Base for errors raised by CoinWatch services."""


class NotAuthorized(CoinWatchError):
    """The actor does not have the permission required for this action."""

    def __init__(self, permission: str) -> None:
        """Record the missing ``permission`` without including secrets."""
        self.permission = permission
        super().__init__(f"missing permission {permission}")


class StaleQuote(CoinWatchError):
    """The last tick is missing or older than the quote freshness window."""

    code: str = "stale_quote"

    def __init__(self, message: str = "last tick is missing or older than 15 seconds") -> None:
        """Record a stable ``code`` of ``stale_quote``."""
        super().__init__(message)


class KillSwitchEngaged(CoinWatchError):
    """New orders are refused because the kill switch is on."""

    code: str = "kill_switch"

    def __init__(self, message: str = "kill switch is engaged") -> None:
        """Record a stable ``code`` of ``kill_switch``."""
        super().__init__(message)


class QuoteRejected(CoinWatchError):
    """A pre-trade quote failed a safety check.

    ``code`` is a stable snake_case reason such as ``reserves``, ``impact``,
    or ``fee_reserve``. The message can change.
    """

    code: str

    def __init__(self, code: str, message: str) -> None:
        """Record the stable ``code`` and a human-readable ``message``."""
        self.code = code
        super().__init__(message)


class AlertSendError(CoinWatchError):
    """The alert transport failed.

    ``returncode`` is the process status when the sender ran a command.
    The message is that code, or a fixed failure line when there is no code.
    It does not include stdout, stderr, or the command line.
    """

    def __init__(self, returncode: int | None) -> None:
        """Record ``returncode`` without process output or the command line."""
        self.returncode = returncode
        if returncode is None:
            super().__init__("alert send failed")
        else:
            super().__init__(str(returncode))


class ChainReadError(CoinWatchError):
    """A chain account could not be read.

    ``failure`` is a stable label such as ``missing``, ``short``, ``invalid``,
    ``mint``, or ``rpc``. ``mint`` is the coin address. The message is only
    that label, so it does not include an RPC URL.
    """

    def __init__(self, failure: str, mint: str) -> None:
        """Record the failure label and mint without copying the RPC URL."""
        self.failure = failure
        self.mint = mint
        super().__init__(failure)


class StrategyError(CoinWatchError):
    """A strategy document is not a single list of allowed comparisons."""


class RequestRejected(CoinWatchError):
    """A control request failed before a domain row was written.

    ``code`` is a stable snake_case reason such as ``invalid_strategy``,
    ``live_disabled``, ``invalid_status``, or ``invalid_request``.
    The message does not include a key, seed, or request body.
    """

    def __init__(self, code: str, message: str) -> None:
        """Record the stable ``code`` and a human-readable ``message``."""
        self.code = code
        super().__init__(message)
