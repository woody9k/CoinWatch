"""``signal-cli`` implementation of the alert port.

The process is started only from ``send``. Tests pass ``runner`` so the
default ``subprocess.run`` is never called.
"""

import subprocess
from collections.abc import Callable

from coinwatch.errors import AlertSendError

CommandRunner = Callable[..., object]

_TIMEOUT_SECONDS = 30


class SignalCliSender:
    """Send one Signal message by executing ``signal-cli``.

    The argv is ``[bin, "-a", account, "send", "-m", message, recipient]``.
    ``runner`` is called with that argv, ``check=True``, ``capture_output=True``,
    and a timeout of 30 seconds. The default runner is ``subprocess.run``.
    """

    def __init__(
        self,
        bin: str,
        account: str,
        runner: CommandRunner | None = None,
    ) -> None:
        """Store the binary, the linked account, and the command runner."""
        self._bin = bin
        self._account = account
        self._runner = subprocess.run if runner is None else runner

    def send(self, recipient: str, message: str) -> None:
        """Run ``signal-cli send`` for ``recipient``.

        A failed command raises ``AlertSendError``. The error text is the
        return code when one exists, and never stdout or stderr.
        """
        argv = [self._bin, "-a", self._account, "send", "-m", message, recipient]
        try:
            self._runner(argv, check=True, capture_output=True, timeout=_TIMEOUT_SECONDS)
        except subprocess.CalledProcessError as exc:
            raise AlertSendError(exc.returncode) from None
        except subprocess.TimeoutExpired:
            raise AlertSendError(None) from None
        except OSError:
            raise AlertSendError(None) from None
