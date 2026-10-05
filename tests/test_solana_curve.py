"""Bonding-curve decoding without a network call."""

import struct
from decimal import Decimal

import pytest

from coinwatch.chains.solana import (
    SolanaPumpAdapter,
    bonding_curve_address,
    decode_bonding_curve,
)
from coinwatch.errors import ChainReadError

BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
BOBCOIN_CURVE = "4MDH9YjYux66d6iHqfc5KttpfHGga9QAG8mA6qGVpTYG"
SOL_USD = Decimal(150)
# 30 SOL of virtual liquidity against 1_000_000 tokens.
_VIRTUAL_TOKEN = 1_000_000_000_000
_VIRTUAL_SOL = 30_000_000_000
# 25% sold of the 793_100_000_000_000 initial real token reserves.
_REAL_TOKEN = 594_825_000_000_000
_REAL_SOL = 5_500_000_000
_TOTAL_SUPPLY = 1_000_000_000_000_000


def _account(
    *,
    virtual_token: int = _VIRTUAL_TOKEN,
    virtual_sol: int = _VIRTUAL_SOL,
    real_token: int = _REAL_TOKEN,
    real_sol: int = _REAL_SOL,
    total_supply: int = _TOTAL_SUPPLY,
    complete: bool = False,
) -> bytes:
    """Build discriminator plus curve fields, with room for a later creator pubkey."""
    body = struct.pack(
        "<QQQQQ?",
        virtual_token,
        virtual_sol,
        real_token,
        real_sol,
        total_supply,
        complete,
    )
    return b"\x00" * 8 + body + b"\xab" * 32


def test_decode_price_liquidity_mcap_and_curve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Price, liquidity, market cap, and curve percent match hand-computed Decimals.

    virtual SOL 30 / virtual tokens 1_000_000 = 0.00003 native.
    Real SOL reserves 5_500_000_000 are 5.5 SOL of liquidity.
    Market cap is 0.00003 * 1_000_000_000 supply tokens * 150 USD = 4_500_000.
    Sold reserves 198_275_000_000_000 / 793_100_000_000_000 = 25%.
    """
    monkeypatch.setattr("coinwatch.chains.solana.AsyncClient", _forbid_rpc)
    state = decode_bonding_curve(_account(), SOL_USD, mint=BOBCOIN)
    assert state.price_native == Decimal("0.00003")
    assert state.price_usd == Decimal("0.0045")
    assert state.liquidity_native == Decimal("5.5")
    assert state.mcap_usd == Decimal(4500000)
    assert state.curve_pct == Decimal(25)
    assert state.complete is False
    assert state.volume_1m == Decimal(0)
    assert state.volume_5m == Decimal(0)
    assert state.volume_15m == Decimal(0)
    assert state.holders is None
    assert isinstance(state.price_native, Decimal)
    assert not isinstance(state.price_native, float)


def test_complete_curve_is_100(monkeypatch: pytest.MonkeyPatch) -> None:
    """A completed curve reports 100% even when reserves would be 25%."""
    monkeypatch.setattr("coinwatch.chains.solana.AsyncClient", _forbid_rpc)
    state = decode_bonding_curve(_account(complete=True), SOL_USD, mint=BOBCOIN)
    assert state.complete is True
    assert state.curve_pct == Decimal(100)
    assert state.price_native == Decimal("0.00003")


def test_curve_pct_clamps_to_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Progress below 0 or above 100 is clamped."""
    monkeypatch.setattr("coinwatch.chains.solana.AsyncClient", _forbid_rpc)
    oversold = decode_bonding_curve(_account(real_token=0), SOL_USD, mint=BOBCOIN)
    untouched = decode_bonding_curve(
        _account(real_token=793_100_000_000_000 + 5_000),
        SOL_USD,
        mint=BOBCOIN,
    )
    assert oversold.curve_pct == Decimal(100)
    assert untouched.curve_pct == Decimal(0)


def test_short_or_unpriceable_account_raises() -> None:
    """A short body or a zero virtual token reserve is a chain read error."""
    with pytest.raises(ChainReadError) as short:
        decode_bonding_curve(b"\x00" * 48, SOL_USD, mint=BOBCOIN)
    assert short.value.failure == "short"
    assert short.value.mint == BOBCOIN
    assert "http" not in str(short.value)

    with pytest.raises(ChainReadError) as invalid:
        decode_bonding_curve(_account(virtual_token=0), SOL_USD, mint=BOBCOIN)
    assert invalid.value.failure == "invalid"


def test_reader_path_does_not_call_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decoding supplied account bytes does not construct an RPC client."""
    monkeypatch.setattr("coinwatch.chains.solana.AsyncClient", _forbid_rpc)
    adapter = SolanaPumpAdapter(
        "http://127.0.0.1:9",
        SOL_USD,
        account_reader=lambda _mint: _account(),
    )
    state = adapter.get_coin_state(BOBCOIN)
    assert state.mcap_usd == Decimal(4500000)
    assert state.liquidity_native == Decimal("5.5")

    missing = SolanaPumpAdapter(
        "http://127.0.0.1:9",
        SOL_USD,
        account_reader=lambda _mint: None,
    )
    with pytest.raises(ChainReadError) as raised:
        missing.get_coin_state(BOBCOIN)
    assert raised.value.failure == "missing"
    assert raised.value.mint == BOBCOIN


def test_bonding_curve_pda() -> None:
    """BobCoin maps to the pump.fun bonding-curve PDA."""
    assert bonding_curve_address(BOBCOIN) == BOBCOIN_CURVE
    with pytest.raises(ChainReadError) as raised:
        bonding_curve_address("not-a-mint")
    assert raised.value.failure == "mint"


def _forbid_rpc(*_args: object, **_kwargs: object) -> object:
    """Fail the test when the Solana RPC client is constructed."""
    raise AssertionError("rpc client constructed")
