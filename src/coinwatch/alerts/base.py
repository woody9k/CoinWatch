"""Alert port used by services.

This is the only alert port. Trade code must not shell out itself.
"""

from typing import Protocol


class AlertSender(Protocol):
    """This is the only alert port. Trade code must not shell out itself."""

    def send(self, recipient: str, message: str) -> None:
        """Deliver ``message`` to ``recipient``."""
