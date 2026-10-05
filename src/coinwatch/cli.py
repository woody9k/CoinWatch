"""Quote the latest stored BobCoin tick. No network call and no private key.

``quote-buy`` spends SOL. ``quote-sell`` spends tokens. Both print the quote
as JSON. A refusal prints ``{"error": {"code": "...", "message": "..."}}``
and exits 2. The kill switch is ``COINWATCH_KILL_SWITCH`` (default false).
"""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.db.models import Tick
from coinwatch.db.session import create_engine
from coinwatch.errors import KillSwitchEngaged, QuoteRejected, StaleQuote
from coinwatch.quoting import Quote, quote_buy, quote_sell
from coinwatch.settings import get_settings

BOBCOIN = "BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump"
CHAIN = "solana"


def main(argv: list[str] | None = None) -> int:
    """Parse ``quote-buy`` or ``quote-sell`` and print one JSON object."""
    parser = argparse.ArgumentParser(
        prog="coinwatch.cli",
        description="Quote the latest BobCoin tick. Nothing is sent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    buy = sub.add_parser("quote-buy", help="Quote a SOL buy of BobCoin")
    _add_common(buy)
    sell = sub.add_parser("quote-sell", help="Quote a token sell of BobCoin")
    _add_common(sell)
    sell.add_argument("--position-size", required=True, type=_decimal)
    args = parser.parse_args(argv)
    try:
        quote = _quote(args)
    except (KillSwitchEngaged, StaleQuote, QuoteRejected) as exc:
        print(json.dumps({"error": {"code": exc.code, "message": str(exc)}}))
        return 2
    print(json.dumps(_payload(quote)))
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    """Add the flags shared by buy and sell quotes."""
    parser.add_argument("--amount", required=True, type=_decimal)
    parser.add_argument("--balance", required=True, type=_decimal)
    parser.add_argument("--slippage-pct", type=_decimal, default=Decimal(10))
    parser.add_argument("--max-impact-pct", type=_decimal, default=Decimal(10))


def _quote(args: argparse.Namespace) -> Quote:
    """Load the latest BobCoin tick and quote it. Does not open an RPC client."""
    settings = get_settings()
    engine = create_engine()
    with Session(engine) as session:
        tick = session.scalar(
            select(Tick)
            .where(Tick.chain == CHAIN, Tick.coin_address == BOBCOIN)
            .order_by(Tick.ts.desc())
            .limit(1)
        )
        observed_at = None if tick is None else tick.ts
        virtual_sol = None if tick is None else tick.virtual_sol_reserves
        virtual_token = None if tick is None else tick.virtual_token_reserves
        # Ticks do not store the curve complete flag. Callers with a CoinState
        # pass that flag into quote_buy or quote_sell themselves.
        now = datetime.now(UTC)
        balance = cast(Decimal, args.balance)
        slippage_pct = cast(Decimal, args.slippage_pct)
        max_impact_pct = cast(Decimal, args.max_impact_pct)
        amount = cast(Decimal, args.amount)
        if args.command == "quote-buy":
            return quote_buy(
                amount,
                virtual_sol=virtual_sol,
                virtual_token=virtual_token,
                observed_at=observed_at,
                now=now,
                complete=False,
                wallet_balance_sol=balance,
                kill_switch=settings.coinwatch_kill_switch,
                slippage_pct=slippage_pct,
                max_impact_pct=max_impact_pct,
            )
        return quote_sell(
            amount,
            virtual_sol=virtual_sol,
            virtual_token=virtual_token,
            observed_at=observed_at,
            now=now,
            complete=False,
            wallet_balance_sol=balance,
            position_size=cast(Decimal, args.position_size),
            kill_switch=settings.coinwatch_kill_switch,
            slippage_pct=slippage_pct,
            max_impact_pct=max_impact_pct,
        )


def _payload(quote: Quote) -> dict[str, str]:
    """Return quote fields as decimal strings without trailing zeros."""
    return {
        "side": quote.side,
        "amount_in": _decimal_str(quote.amount_in),
        "expected_out": _decimal_str(quote.expected_out),
        "minimum_out": _decimal_str(quote.minimum_out),
        "fee_native": _decimal_str(quote.fee_native),
        "price_impact_pct": _decimal_str(quote.price_impact_pct),
        "sol_debited": _decimal_str(quote.sol_debited),
        "sol_credited": _decimal_str(quote.sol_credited),
    }


def _decimal_str(value: Decimal) -> str:
    """Render ``value`` in fixed point, dropping trailing zeros."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if text else "0"


def _decimal(value: str) -> Decimal:
    """Parse a CLI decimal. Rejects non-numeric text."""
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("expected a decimal number") from exc


if __name__ == "__main__":
    raise SystemExit(main())
