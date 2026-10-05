"""Bonding-curve quotes with no network and no private key."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from coinwatch.errors import KillSwitchEngaged, QuoteRejected, StaleQuote
from coinwatch.quoting import quote_buy, quote_sell

_NOW = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)
_VIRTUAL_SOL = Decimal(30)
_VIRTUAL_TOKEN = Decimal(1_000_000)


def test_buy_matches_hand_computed_constant_product() -> None:
    """One SOL into 30 SOL and 1_000_000 tokens, with a 1 percent fee.

    fee = 1 * 100 / 10000 = 0.01
    sol_after_fee = 0.99
    k = 30_000_000
    new_sol = 30.99
    tokens_out = 1_000_000 - 30_000_000 / 30.99
    = 31945.7889641819941916747338..., rounded down to 18 places.
    Spot is 30 / 1_000_000. Impact uses the rounded tokens out.
    Minimum out is that output times 0.90, rounded down.
    """
    quote = quote_buy(Decimal(1), **_market())
    assert quote.side == "buy"
    assert quote.amount_in == Decimal(1)
    assert quote.fee_native == Decimal("0.01")
    assert quote.expected_out == Decimal("31945.788964181994191674")
    assert quote.minimum_out == Decimal("28751.210067763794772506")
    assert quote.price_impact_pct == Decimal("4.343434343434343434")
    assert quote.sol_debited == Decimal(1)
    assert quote.sol_credited == Decimal(0)
    assert isinstance(quote.expected_out, Decimal)
    assert not isinstance(quote.expected_out, float)


def test_sell_matches_hand_computed_constant_product() -> None:
    """10_000 tokens out of 30 SOL and 1_000_000 tokens, with a 1 percent fee.

    k = 30_000_000
    new_token = 1_010_000
    sol_gross = 30 - 30_000_000 / 1_010_000
    fee = sol_gross * 0.01
    sol_out = sol_gross - fee = 0.2940594059405940594059..., rounded down.
    Impact uses that post-fee SOL out. Minimum out is 90 percent, rounded down.
    """
    quote = quote_sell(Decimal(10_000), position_size=Decimal(10_000), **_market())
    assert quote.side == "sell"
    assert quote.amount_in == Decimal(10_000)
    assert quote.fee_native == Decimal("0.002970297029702970")
    assert quote.expected_out == Decimal("0.294059405940594059")
    assert quote.minimum_out == Decimal("0.264653465346534653")
    assert quote.price_impact_pct == Decimal("2.020202020202020342")
    assert quote.sol_debited == Decimal(0)
    assert quote.sol_credited == quote.expected_out
    assert isinstance(quote.expected_out, Decimal)
    assert not isinstance(quote.expected_out, float)


def test_kill_switch_wins_over_a_missing_tick() -> None:
    """The kill switch is the first refusal, before the stale-tick check."""
    with pytest.raises(KillSwitchEngaged) as raised:
        quote_buy(Decimal(1), **_market(observed_at=None, kill_switch=True))
    assert raised.value.code == "kill_switch"


def test_stale_tick_is_refused_before_reserves() -> None:
    """A tick older than 15 seconds is stale even when reserves are missing."""
    stale = _NOW - timedelta(seconds=16)
    with pytest.raises(StaleQuote):
        quote_buy(Decimal(1), **_market(observed_at=stale, virtual_sol=None))
    with pytest.raises(StaleQuote):
        quote_buy(Decimal(1), **_market(observed_at=None))


def test_missing_or_zero_reserves_are_refused() -> None:
    """A fresh tick with no usable virtual reserves is rejected."""
    cases = (
        {"virtual_sol": None, "virtual_token": _VIRTUAL_TOKEN},
        {"virtual_sol": _VIRTUAL_SOL, "virtual_token": None},
        {"virtual_sol": Decimal(0), "virtual_token": _VIRTUAL_TOKEN},
        {"virtual_sol": _VIRTUAL_SOL, "virtual_token": Decimal(0)},
    )
    for overrides in cases:
        with pytest.raises(QuoteRejected) as raised:
            quote_buy(Decimal(1), **_market(**overrides))
        assert raised.value.code == "reserves"


def test_complete_coin_is_refused_before_slippage() -> None:
    """A migrated coin is refused even when slippage is also above the cap."""
    with pytest.raises(QuoteRejected) as raised:
        quote_buy(Decimal(1), **_market(complete=True, slippage_pct=Decimal(16)))
    assert raised.value.code == "venue_migrated"


def test_slippage_above_15_is_refused_before_impact() -> None:
    """Slippage of 16 is refused before a trade that would also exceed impact."""
    with pytest.raises(QuoteRejected) as raised:
        quote_buy(
            Decimal(10),
            **_market(slippage_pct=Decimal(16), wallet_balance_sol=Decimal("0.10")),
        )
    assert raised.value.code == "slippage_cap"


def test_impact_above_the_cap_is_refused_before_the_fee_reserve() -> None:
    """A 10 SOL buy moves this curve more than 10 percent."""
    with pytest.raises(QuoteRejected) as raised:
        quote_buy(Decimal(10), **_market(wallet_balance_sol=Decimal("0.10")))
    assert raised.value.code == "impact"


def test_buy_that_leaves_less_than_the_fee_reserve_is_refused() -> None:
    """Spending 1 SOL from 1.04 SOL leaves less than the 0.05 SOL reserve."""
    with pytest.raises(QuoteRejected) as raised:
        quote_buy(Decimal(1), **_market(wallet_balance_sol=Decimal("1.04")))
    assert raised.value.code == "fee_reserve"


def test_sell_with_balance_below_the_fee_reserve_is_refused() -> None:
    """A 0.01 SOL balance cannot pay the transaction fee, even for a small sell."""
    with pytest.raises(QuoteRejected) as raised:
        quote_sell(
            Decimal(10_000),
            position_size=Decimal(1),
            **_market(wallet_balance_sol=Decimal("0.01")),
        )
    assert raised.value.code == "fee_reserve"


def test_sell_larger_than_the_position_is_refused() -> None:
    """A sell cannot spend more tokens than the position holds."""
    with pytest.raises(QuoteRejected) as raised:
        quote_sell(
            Decimal(10_000),
            position_size=Decimal(9999),
            **_market(wallet_balance_sol=Decimal(1)),
        )
    assert raised.value.code == "position"


def _market(**overrides: object) -> dict[str, object]:
    """Return keyword arguments for a fresh quote on the hand-computed reserves."""
    values: dict[str, object] = {
        "virtual_sol": _VIRTUAL_SOL,
        "virtual_token": _VIRTUAL_TOKEN,
        "observed_at": _NOW,
        "now": _NOW,
        "complete": False,
        "wallet_balance_sol": Decimal(10),
    }
    values.update(overrides)
    return values
