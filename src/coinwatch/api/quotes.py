"""Quote preview route.

A preview shows expected output, the protocol fee, and price impact. It does
not submit a trade, write an audit row, or open an RPC client.
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.api.app import SESSION_COOKIE, error_response, request_id_of, request_session
from coinwatch.db.models import Coin, Tick, User
from coinwatch.errors import KillSwitchEngaged, QuoteRejected, StaleQuote
from coinwatch.quoting import Quote, quote_buy, quote_sell
from coinwatch.services.identity import get_session_user, require_permission
from coinwatch.settings import get_settings

router = APIRouter()

_CHAIN = "solana"


async def post_quote(request: Request) -> Response:
    """Preview one curve quote and return money fields as decimal strings.

    Requires ``trades.read`` before the coin lookup. ``amount`` is SOL on a
    buy and tokens on a sell. A sell also requires ``position_size``, which is
    the open token size checked by the quote and is not read from a position
    row. An unknown coin is 404. ``KillSwitchEngaged``, ``StaleQuote``, and
    ``QuoteRejected`` are 422. This route does not audit the preview and does
    not insert a trade.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    body = await _json_body(request)
    require_permission(
        db,
        user,
        "trades.read",
        entity_type="coin",
        entity_id=_coin_entity_id(_address_hint(body)),
        request_id=request_id_of(request),
    )
    parsed = _parsed_quote(body)
    if isinstance(parsed, JSONResponse):
        return parsed
    coin = db.get(Coin, (_CHAIN, parsed.coin_address))
    if coin is None:
        return error_response(404, "not_found", "Not found.")
    try:
        quote = _quote_coin(db, coin, parsed, now=datetime.now(UTC))
    except (KillSwitchEngaged, StaleQuote, QuoteRejected) as exc:
        return error_response(422, exc.code, str(exc))
    return JSONResponse(content=_quote_item(quote))


def _session_user(request: Request) -> tuple[Session, User] | JSONResponse:
    """Return the live session user, or 401 when the cookie is missing or dead."""
    db = request_session(request)
    if db is None:
        return error_response(500, "internal", "Internal server error.")
    cookie = request.cookies.get(SESSION_COOKIE)
    user = get_session_user(db, cookie) if cookie else None
    if user is None:
        return error_response(401, "unauthenticated", "Authentication required.")
    return db, user


def _address_hint(body: object) -> str:
    """Return a coin address from the body when one was sent as a string."""
    if not isinstance(body, dict):
        return ""
    address = body.get("coin_address")
    if isinstance(address, str):
        return address
    return ""


def _parsed_quote(body: object) -> "_QuoteRequest | JSONResponse":
    """Validate side, amount, and sell position size. Does not look up a coin."""
    if not isinstance(body, dict):
        return error_response(422, "invalid_request", "Invalid request.")
    raw_side = body.get("side")
    if raw_side == "buy":
        side: Literal["buy", "sell"] = "buy"
    elif raw_side == "sell":
        side = "sell"
    else:
        return error_response(422, "invalid_request", "Invalid request.")
    address = body.get("coin_address")
    if not isinstance(address, str):
        return error_response(422, "invalid_request", "Invalid request.")
    amount = _positive_decimal(body.get("amount"))
    if amount is None:
        return error_response(422, "invalid_amount", "Amount must be a positive decimal.")
    position_size: Decimal | None = None
    if side == "sell":
        if "position_size" not in body:
            return error_response(422, "invalid_amount", "Sell requires a position size.")
        position_size = _finite_decimal(body.get("position_size"))
        if position_size is None:
            return error_response(422, "invalid_amount", "Position size must be a decimal.")
    return _QuoteRequest(
        side=side,
        coin_address=address,
        amount=amount,
        position_size=position_size,
    )


def _quote_coin(db: Session, coin: Coin, parsed: "_QuoteRequest", *, now: datetime) -> Quote:
    """Quote ``parsed`` against the latest tick. Does not open an RPC client."""
    tick = db.scalar(
        select(Tick)
        .where(Tick.chain == coin.chain, Tick.coin_address == coin.address)
        .order_by(Tick.ts.desc(), Tick.id.desc())
        .limit(1)
    )
    observed_at = None if tick is None else tick.ts
    virtual_sol = None if tick is None else tick.virtual_sol_reserves
    virtual_token = None if tick is None else tick.virtual_token_reserves
    complete = coin.status == "graduated" or coin.graduated_at is not None
    settings = get_settings()
    if parsed.side == "buy":
        return quote_buy(
            parsed.amount,
            virtual_sol=virtual_sol,
            virtual_token=virtual_token,
            observed_at=observed_at,
            now=now,
            complete=complete,
            wallet_balance_sol=settings.paper_balance_sol,
            kill_switch=settings.coinwatch_kill_switch,
        )
    if parsed.position_size is None:
        raise QuoteRejected("position", "sell size is greater than the position")
    return quote_sell(
        parsed.amount,
        virtual_sol=virtual_sol,
        virtual_token=virtual_token,
        observed_at=observed_at,
        now=now,
        complete=complete,
        wallet_balance_sol=settings.paper_balance_sol,
        position_size=parsed.position_size,
        kill_switch=settings.coinwatch_kill_switch,
    )


def _quote_item(quote: Quote) -> dict[str, str]:
    """Serialize a quote. Every money field is a decimal string."""
    return {
        "side": quote.side,
        "amount_in": _decimal_string(quote.amount_in),
        "expected_out": _decimal_string(quote.expected_out),
        "minimum_out": _decimal_string(quote.minimum_out),
        "fee_native": _decimal_string(quote.fee_native),
        "price_impact_pct": _decimal_string(quote.price_impact_pct),
        "sol_debited": _decimal_string(quote.sol_debited),
        "sol_credited": _decimal_string(quote.sol_credited),
    }


def _positive_decimal(value: object) -> Decimal | None:
    """Return a finite decimal greater than zero, or ``None`` when it is not."""
    parsed = _finite_decimal(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _finite_decimal(value: object) -> Decimal | None:
    """Return a finite decimal parsed from a string, or ``None``."""
    if not isinstance(value, str):
        return None
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _decimal_string(value: Decimal) -> str:
    """Render a decimal as a JSON string without scientific notation."""
    return format(value, "f")


def _coin_entity_id(address: str) -> str:
    """Build an audit entity id that fits the audit column."""
    return f"{_CHAIN}:{address}"[:128]


async def _json_body(request: Request) -> object:
    """Return parsed JSON, or ``None`` when the body is not JSON."""
    try:
        body: object = await request.json()
    except ValueError:
        return None
    return body


class _QuoteRequest:
    """Fields accepted by ``POST /api/quotes`` after validation."""

    def __init__(
        self,
        *,
        side: Literal["buy", "sell"],
        coin_address: str,
        amount: Decimal,
        position_size: Decimal | None,
    ) -> None:
        """Store one validated preview request."""
        self.side: Literal["buy", "sell"] = side
        self.coin_address = coin_address
        self.amount = amount
        self.position_size = position_size


router.post("/api/quotes")(post_quote)
