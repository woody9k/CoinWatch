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
    """The last tick is older than the quote freshness window."""


class KillSwitchEngaged(CoinWatchError):
    """New orders are refused because the kill switch is on."""


class QuoteRejected(CoinWatchError):
    """A pre-trade quote failed a safety check."""


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
