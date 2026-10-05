"""signal-cli argv construction. The runner is a fake and no process is started."""

import subprocess

import pytest

from coinwatch.alerts.signal_cli import SignalCliSender
from coinwatch.errors import AlertSendError

ACCOUNT = "+14075150936"
MESSAGE = "CoinWatch test alert"


def test_send_builds_signal_cli_argv() -> None:
    """The sender asks the runner once for the signal-cli send command."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv: list[str], **kwargs: object) -> None:
        calls.append((argv, kwargs))

    sender = SignalCliSender("signal-cli", ACCOUNT, runner=runner)
    sender.send(ACCOUNT, MESSAGE)
    assert calls == [
        (
            ["signal-cli", "-a", ACCOUNT, "send", "-m", MESSAGE, ACCOUNT],
            {"check": True, "capture_output": True, "timeout": 30},
        )
    ]


def test_failed_send_hides_process_output() -> None:
    """A failed command reports the return code and omits stdout and stderr."""

    def runner(argv: list[str], **kwargs: object) -> None:
        del argv, kwargs
        raise subprocess.CalledProcessError(
            3,
            ["signal-cli"],
            output=b"stdout-phone-+14075150936",
            stderr=b"stderr-phone-+14075150936",
        )

    sender = SignalCliSender("signal-cli", ACCOUNT, runner=runner)
    with pytest.raises(AlertSendError) as caught:
        sender.send(ACCOUNT, MESSAGE)
    text = str(caught.value)
    assert text == "3"
    assert "stdout-phone" not in text
    assert "stderr-phone" not in text
    assert caught.value.returncode == 3
