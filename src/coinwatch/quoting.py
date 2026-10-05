"""Constant-product quotes for a pump.fun bonding curve.

Quotes are pure: they do not read the database or send a transaction.
The protocol fee is 100 basis points (1 percent), charged in SOL. The
creator fee is not modeled. A buy takes the fee from SOL in before the
swap. A sell takes the fee from gross SOL out after the swap.

Refusals run in this order: kill switch, stale tick, missing or zero
virtual reserves, migrated coin, slippage above 15 percent, price impact
above the cap, fee reserve, sell larger than the position, and a
non-positive output.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Literal

from coinwatch.errors import KillSwitchEngaged, QuoteRejected, StaleQuote
from coinwatch.services.poll import is_stale

_PROTOCOL_FEE_BPS = Decimal(100)
_BPS = Decimal(10000)
_HUNDRED = Decimal(100)
_SLIPPAGE_CAP_PCT = Decimal(15)
_ZERO = Decimal(0)
_ONE = Decimal(1)
_MONEY = Decimal("0.000000000000000001")

DEFAULT_SLIPPAGE_PCT = Decimal(10)
DEFAULT_MAX_IMPACT_PCT = Decimal(10)
DEFAULT_FEE_RESERVE = Decimal("0.05")


@dataclass(frozen=True, slots=True)
class Quote:
    """Preview of one buy or sell. Amounts are ``Decimal`` values.

    ``amount_in`` is SOL on a buy and tokens on a sell. ``expected_out`` is
    tokens on a buy and SOL on a sell. ``fee_native`` is the protocol fee in
    SOL. The creator fee is not included. ``sol_debited`` is ``amount_in`` on
    a buy and zero on a sell. ``sol_credited`` is the post-fee SOL out on a
    sell and zero on a buy.
    """

    side: Literal["buy", "sell"]
    amount_in: Decimal
    expected_out: Decimal
    minimum_out: Decimal
    fee_native: Decimal
    price_impact_pct: Decimal
    sol_debited: Decimal
    sol_credited: Decimal


def quote_buy(
    sol_in: Decimal,
    *,
    virtual_sol: Decimal | None,
    virtual_token: Decimal | None,
    observed_at: datetime | None,
    now: datetime,
    complete: bool,
    wallet_balance_sol: Decimal,
    kill_switch: bool = False,
    slippage_pct: Decimal = DEFAULT_SLIPPAGE_PCT,
    max_impact_pct: Decimal = DEFAULT_MAX_IMPACT_PCT,
    fee_reserve: Decimal = DEFAULT_FEE_RESERVE,
) -> Quote:
    """Quote a buy of ``sol_in`` SOL against the virtual reserves.

    The creator fee is not modeled. ``observed_at`` is the tick timestamp, or
    ``None`` when there is no tick. ``now`` and ``observed_at`` are
    timezone-aware. Money outputs are quantized to 18 decimal places, rounding
    down. Raises ``KillSwitchEngaged``, ``StaleQuote``, or ``QuoteRejected``.
    """
    reserves_sol, reserves_token = _guard(
        virtual_sol=virtual_sol,
        virtual_token=virtual_token,
        observed_at=observed_at,
        now=now,
        complete=complete,
        kill_switch=kill_switch,
        slippage_pct=slippage_pct,
    )
    fee, tokens_out = _buy_fill(sol_in, reserves_sol, reserves_token)
    expected_out = _money(tokens_out)
    impact = _buy_impact(sol_in, expected_out, reserves_sol, reserves_token)
    _reject_impact(impact, max_impact_pct)
    if wallet_balance_sol - sol_in < fee_reserve:
        raise QuoteRejected("fee_reserve", "wallet would keep less than the fee reserve")
    if expected_out <= _ZERO or impact is None:
        raise QuoteRejected("unfilled", "quote output is not positive")
    return Quote(
        side="buy",
        amount_in=sol_in,
        expected_out=expected_out,
        minimum_out=_minimum_out(expected_out, slippage_pct),
        fee_native=_money(fee),
        price_impact_pct=impact,
        sol_debited=sol_in,
        sol_credited=_ZERO,
    )


def quote_sell(
    token_in: Decimal,
    *,
    virtual_sol: Decimal | None,
    virtual_token: Decimal | None,
    observed_at: datetime | None,
    now: datetime,
    complete: bool,
    wallet_balance_sol: Decimal,
    position_size: Decimal,
    kill_switch: bool = False,
    slippage_pct: Decimal = DEFAULT_SLIPPAGE_PCT,
    max_impact_pct: Decimal = DEFAULT_MAX_IMPACT_PCT,
    fee_reserve: Decimal = DEFAULT_FEE_RESERVE,
) -> Quote:
    """Quote a sell of ``token_in`` tokens against the virtual reserves.

    The creator fee is not modeled. Impact uses the post-fee SOL out.
    ``position_size`` is the token balance the sell is allowed to spend.
    ``now`` and ``observed_at`` are timezone-aware. Money outputs are quantized
    to 18 decimal places, rounding down. Raises ``KillSwitchEngaged``,
    ``StaleQuote``, or ``QuoteRejected``.
    """
    reserves_sol, reserves_token = _guard(
        virtual_sol=virtual_sol,
        virtual_token=virtual_token,
        observed_at=observed_at,
        now=now,
        complete=complete,
        kill_switch=kill_switch,
        slippage_pct=slippage_pct,
    )
    fee, sol_out = _sell_fill(token_in, reserves_sol, reserves_token)
    expected_out = _money(sol_out)
    impact = _sell_impact(token_in, expected_out, reserves_sol, reserves_token)
    _reject_impact(impact, max_impact_pct)
    if wallet_balance_sol < fee_reserve:
        raise QuoteRejected("fee_reserve", "wallet would keep less than the fee reserve")
    if token_in > position_size:
        raise QuoteRejected("position", "sell size is greater than the position")
    if expected_out <= _ZERO or impact is None:
        raise QuoteRejected("unfilled", "quote output is not positive")
    return Quote(
        side="sell",
        amount_in=token_in,
        expected_out=expected_out,
        minimum_out=_minimum_out(expected_out, slippage_pct),
        fee_native=_money(fee),
        price_impact_pct=impact,
        sol_debited=_ZERO,
        sol_credited=expected_out,
    )


def _guard(
    *,
    virtual_sol: Decimal | None,
    virtual_token: Decimal | None,
    observed_at: datetime | None,
    now: datetime,
    complete: bool,
    kill_switch: bool,
    slippage_pct: Decimal,
) -> tuple[Decimal, Decimal]:
    """Apply the refusals that do not need the swap result."""
    if kill_switch:
        raise KillSwitchEngaged()
    if is_stale(observed_at, now):
        raise StaleQuote()
    if (
        virtual_sol is None
        or virtual_token is None
        or virtual_sol <= _ZERO
        or virtual_token <= _ZERO
    ):
        raise QuoteRejected("reserves", "virtual reserves are missing or zero")
    if complete:
        raise QuoteRejected("venue_migrated", "coin has left the bonding curve")
    if slippage_pct > _SLIPPAGE_CAP_PCT:
        raise QuoteRejected("slippage_cap", "slippage percent is above 15")
    return virtual_sol, virtual_token


def _buy_fill(
    sol_in: Decimal, virtual_sol: Decimal, virtual_token: Decimal
) -> tuple[Decimal, Decimal]:
    """Return the protocol fee and tokens out for a buy."""
    fee = sol_in * _PROTOCOL_FEE_BPS / _BPS
    sol_after_fee = sol_in - fee
    new_sol = virtual_sol + sol_after_fee
    if new_sol <= _ZERO:
        return fee, _ZERO
    k = virtual_token * virtual_sol
    new_token = k / new_sol
    return fee, virtual_token - new_token


def _sell_fill(
    token_in: Decimal, virtual_sol: Decimal, virtual_token: Decimal
) -> tuple[Decimal, Decimal]:
    """Return the protocol fee and post-fee SOL out for a sell."""
    new_token = virtual_token + token_in
    if new_token <= _ZERO:
        return _ZERO, _ZERO
    k = virtual_token * virtual_sol
    new_sol = k / new_token
    sol_gross = virtual_sol - new_sol
    fee = sol_gross * _PROTOCOL_FEE_BPS / _BPS
    return fee, sol_gross - fee


def _buy_impact(
    sol_in: Decimal,
    tokens_out: Decimal,
    virtual_sol: Decimal,
    virtual_token: Decimal,
) -> Decimal | None:
    """Return buy impact in percent, or ``None`` when output is not positive."""
    if tokens_out <= _ZERO:
        return None
    spot = virtual_sol / virtual_token
    impact = ((sol_in / tokens_out) / spot - _ONE) * _HUNDRED
    return _clamp_impact(impact)


def _sell_impact(
    token_in: Decimal,
    sol_out: Decimal,
    virtual_sol: Decimal,
    virtual_token: Decimal,
) -> Decimal | None:
    """Return sell impact in percent using post-fee SOL out."""
    if sol_out <= _ZERO or token_in <= _ZERO:
        return None
    spot = virtual_sol / virtual_token
    impact = (spot / (sol_out / token_in) - _ONE) * _HUNDRED
    return _clamp_impact(impact)


def _clamp_impact(impact: Decimal) -> Decimal:
    """Quantize impact and clamp a rounding remainder below zero."""
    if impact < _ZERO:
        return _ZERO
    return _money(impact)


def _reject_impact(impact: Decimal | None, max_impact_pct: Decimal) -> None:
    """Refuse when a computed impact is above ``max_impact_pct``."""
    if impact is not None and impact > max_impact_pct:
        raise QuoteRejected("impact", "price impact is above the cap")


def _minimum_out(expected_out: Decimal, slippage_pct: Decimal) -> Decimal:
    """Return expected out reduced by ``slippage_pct``, rounding down."""
    return _money(expected_out * (_ONE - slippage_pct / _HUNDRED))


def _money(value: Decimal) -> Decimal:
    """Quantize a money amount to 18 decimal places, rounding down."""
    return value.quantize(_MONEY, rounding=ROUND_DOWN)
