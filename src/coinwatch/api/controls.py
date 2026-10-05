"""Paper wallet, strategy, bot, and price-alert control routes.

Each route requires a live session and checks its permission before a query
or a write. These routes do not step a bot, send alerts, or accept private keys.
"""

import json
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from coinwatch.api.app import SESSION_COOKIE, error_response, request_id_of, request_session
from coinwatch.db.models import Bot, PriceAlert, Strategy, User, Wallet
from coinwatch.errors import RequestRejected
from coinwatch.services.controls import (
    UNREADABLE_BODY,
    create_bot,
    create_price_alert,
    create_strategy,
    create_wallet,
    list_bots,
    list_strategies,
    list_wallets,
    transition_bot,
)
from coinwatch.services.identity import get_session_user

router = APIRouter()


async def post_wallet(request: Request) -> Response:
    """Create a public wallet.

    Requires ``wallets.manage``. The chain must already exist. A body key
    whose name includes a secret marker is 422 and nothing is stored.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    try:
        wallet = create_wallet(
            db,
            user,
            await _json_body(request),
            request_id=request_id_of(request),
        )
    except RequestRejected as exc:
        return _rejection(exc)
    return JSONResponse(content=_wallet_item(wallet))


async def get_wallets(request: Request) -> Response:
    """Return id, chain, label, and public_address for each wallet.

    Requires ``wallets.read`` before the query. An empty table is an empty
    list. No other fields are included.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    rows = list_wallets(db, user, request_id=request_id_of(request))
    return JSONResponse(content=[_wallet_item(row) for row in rows])


async def post_strategy(request: Request) -> Response:
    """Create a strategy when the YAML loads.

    Requires ``strategies.write``. YAML ``load_strategy`` rejects is 422
    ``invalid_strategy`` and nothing is stored.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    try:
        strategy = create_strategy(
            db,
            user,
            await _json_body(request),
            request_id=request_id_of(request),
        )
    except RequestRejected as exc:
        return _rejection(exc)
    return JSONResponse(content={"id": strategy.id, "name": strategy.name})


async def get_strategies(request: Request) -> Response:
    """Return id, name, and yaml_config for each strategy.

    Requires ``strategies.read``.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    rows = list_strategies(db, user, request_id=request_id_of(request))
    return JSONResponse(content=[_strategy_item(row) for row in rows])


async def post_bot(request: Request) -> Response:
    """Create a paused paper bot.

    Requires ``bots.control``. ``paper: false`` is 422 ``live_disabled`` and
    nothing is stored. The coin, strategy, and wallet must exist on one chain.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    try:
        bot = create_bot(
            db,
            user,
            await _json_body(request),
            request_id=request_id_of(request),
        )
    except RequestRejected as exc:
        return _rejection(exc)
    return JSONResponse(content=_bot_item(bot))


async def get_bots(request: Request) -> Response:
    """Return bot control fields.

    Requires ``bots.read``. ``armed_rules`` is omitted.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    rows = list_bots(db, user, request_id=request_id_of(request))
    return JSONResponse(content=[_bot_item(row) for row in rows])


async def post_price_alert(request: Request) -> Response:
    """Save one market-cap alert.

    Requires ``alerts.manage`` before the coin lookup. An unknown coin is
    404 ``not_found``. Any other condition is 422 ``invalid_request``. A
    non-positive or non-decimal threshold is 422 ``invalid_amount``. The
    response uses the same public fields as the price-alert list, and
    ``threshold`` is a decimal string. This route does not call signal-cli.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    try:
        alert = create_price_alert(
            db,
            user,
            await _json_body(request),
            request_id=request_id_of(request),
        )
    except RequestRejected as exc:
        return _price_alert_rejection(exc)
    return JSONResponse(content=_price_alert_item(alert))


async def start_bot(request: Request, bot_id: int) -> Response:
    """Move a paused or stopped bot to running.

    Requires ``bots.control``. Any other status is 409 and the row is
    unchanged. A missing bot is 404 after the permission check.
    """
    return await _transition(request, bot_id, "start")


async def pause_bot(request: Request, bot_id: int) -> Response:
    """Move a running bot to paused.

    Requires ``bots.control``. Any other status is 409 and the row is
    unchanged. A missing bot is 404 after the permission check.
    """
    return await _transition(request, bot_id, "pause")


async def stop_bot(request: Request, bot_id: int) -> Response:
    """Move a running or paused bot to stopped.

    Requires ``bots.control``. Any other status is 409 and the row is
    unchanged. A missing bot is 404 after the permission check.
    """
    return await _transition(request, bot_id, "stop")


async def _transition(request: Request, bot_id: int, action: str) -> Response:
    """Run one status change and return the bot, 409, or 404."""
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    try:
        bot = transition_bot(
            db,
            user,
            bot_id,
            action,
            request_id=request_id_of(request),
        )
    except RequestRejected as exc:
        return _rejection(exc)
    if bot is None:
        return error_response(404, "not_found", "Not found.")
    return JSONResponse(content=_bot_item(bot))


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


async def _json_body(request: Request) -> object:
    """Return parsed JSON, or a sentinel when the body is not JSON."""
    try:
        body: object = await request.json()
    except json.JSONDecodeError:
        return UNREADABLE_BODY
    return body


def _rejection(exc: RequestRejected) -> JSONResponse:
    """Map a control rejection onto the CoinWatch error body."""
    status = 409 if exc.code == "invalid_status" else 422
    return error_response(status, exc.code, str(exc))


def _price_alert_rejection(exc: RequestRejected) -> JSONResponse:
    """Map a price-alert rejection to 404 or 422."""
    if exc.code == "not_found":
        return error_response(404, exc.code, str(exc))
    return error_response(422, exc.code, str(exc))


def _price_alert_item(alert: PriceAlert) -> dict[str, object]:
    """Serialize one price alert. ``threshold`` is a decimal string."""
    return {
        "id": alert.id,
        "chain": alert.chain,
        "coin_address": alert.coin_address,
        "condition": alert.condition,
        "threshold": _decimal(alert.threshold),
        "enabled": alert.enabled,
        "created_by": alert.created_by,
    }


def _decimal(value: Decimal) -> str:
    """Render a decimal as a JSON string without scientific notation."""
    return format(value, "f")


def _wallet_item(wallet: Wallet) -> dict[str, object]:
    """Serialize a wallet without key material."""
    return {
        "id": wallet.id,
        "chain": wallet.chain,
        "label": wallet.label,
        "public_address": wallet.public_address,
    }


def _strategy_item(strategy: Strategy) -> dict[str, object]:
    """Serialize a strategy list row."""
    return {"id": strategy.id, "name": strategy.name, "yaml_config": strategy.yaml_config}


def _bot_item(bot: Bot) -> dict[str, object]:
    """Serialize bot control fields. ``armed_rules`` is omitted."""
    return {
        "id": bot.id,
        "chain": bot.chain,
        "coin_address": bot.coin_address,
        "strategy_id": bot.strategy_id,
        "wallet_id": bot.wallet_id,
        "status": bot.status,
        "paper": bot.paper,
        "act_on_inference": bot.act_on_inference,
    }


router.get("/api/wallets")(get_wallets)
router.post("/api/wallets")(post_wallet)
router.post("/api/strategies")(post_strategy)
router.get("/api/strategies")(get_strategies)
router.post("/api/bots")(post_bot)
router.get("/api/bots")(get_bots)
router.post("/api/bots/{bot_id}/start")(start_bot)
router.post("/api/bots/{bot_id}/pause")(pause_bot)
router.post("/api/bots/{bot_id}/stop")(stop_bot)
router.post("/api/price-alerts")(post_price_alert)
