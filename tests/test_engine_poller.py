"""Poller configuration. These tests do not open an RPC connection."""

from decimal import Decimal

import pytest

from coinwatch.engine.__main__ import poller_configured
from coinwatch.settings import Settings


def test_poller_stays_off_without_rpc_or_sol_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty RPC URL or an empty SOL price does not start the poller."""
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    monkeypatch.delenv("COINWATCH_SOL_USD", raising=False)
    assert poller_configured(Settings(_env_file=None)) is False

    monkeypatch.setenv("SOLANA_RPC_URL", "http://127.0.0.1:9")
    assert poller_configured(Settings(_env_file=None)) is False

    monkeypatch.setenv("COINWATCH_SOL_USD", "   ")
    unset = Settings(_env_file=None)
    assert unset.sol_usd is None
    assert poller_configured(unset) is False

    monkeypatch.setenv("COINWATCH_SOL_USD", "150")
    ready = Settings(_env_file=None)
    assert ready.sol_usd == Decimal(150)
    assert ready.solana_rpc_url == "http://127.0.0.1:9"
    assert poller_configured(ready) is True
