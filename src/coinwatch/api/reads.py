"""Read-only routes for coins, ticks, positions, trades, the audit log, and alerts.

Each route requires a live session and checks its permission before a query
result is returned. These routes do not create, update, or delete rows.
"""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.api.app import SESSION_COOKIE, error_response, request_id_of, request_session
from coinwatch.db.models import AuditEvent, Coin, Position, PriceAlert, Tick, Trade, User
from coinwatch.services.identity import get_session_user, require_permission

router = APIRouter()

_TICK_LIMIT_DEFAULT = 100
_TICK_LIMIT_MAX = 500
_AUDIT_LIMIT_DEFAULT = 50
_AUDIT_LIMIT_MAX = 200
_TRADE_LIMIT_DEFAULT = 50
_TRADE_LIMIT_MIN = 1
_TRADE_LIMIT_MAX = 200


async def list_coins(request: Request) -> Response:
    """Return tracked coins and each coin's latest tick.

    Requires ``coins.read``. Decimals and timestamps are JSON strings. A coin
    with no tick has ``tick`` set to null.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    require_permission(
        db,
        user,
        "coins.read",
        entity_type="coin",
        entity_id="",
        request_id=request_id_of(request),
    )
    coins = db.scalars(select(Coin).order_by(Coin.chain, Coin.address)).all()
    return JSONResponse(content=[_coin_item(db, coin) for coin in coins])


async def list_ticks(
    request: Request, chain: str, address: str, limit: int = _TICK_LIMIT_DEFAULT
) -> Response:
    """Return newest ticks for one coin.

    Requires ``ticks.read`` before the coin lookup, so a caller without that
    permission is denied even when the coin is missing. ``limit`` defaults to
    100 and is clamped to 1..500. A missing coin is 404.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    require_permission(
        db,
        user,
        "ticks.read",
        entity_type="coin",
        entity_id=_coin_entity_id(chain, address),
        request_id=request_id_of(request),
    )
    coin = db.get(Coin, (chain, address))
    if coin is None:
        return error_response(404, "not_found", "Not found.")
    rows = db.scalars(
        select(Tick)
        .where(Tick.chain == coin.chain, Tick.coin_address == coin.address)
        .order_by(Tick.ts.desc(), Tick.id.desc())
        .limit(_clamp(limit, _TICK_LIMIT_MAX))
    ).all()
    return JSONResponse(content=[_tick_item(row, history=True) for row in rows])


async def list_audit(request: Request, limit: int = _AUDIT_LIMIT_DEFAULT) -> Response:
    """Return the newest audit rows.

    Requires ``audit.read``. ``limit`` defaults to 50 and is clamped to 1..200.
    The payload is the audit columns only. This route does not read
    ``users.password_hash``.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    require_permission(
        db,
        user,
        "audit.read",
        entity_type="audit_event",
        entity_id="",
        request_id=request_id_of(request),
    )
    rows = db.scalars(
        select(AuditEvent)
        .order_by(AuditEvent.ts.desc(), AuditEvent.id.desc())
        .limit(_clamp(limit, _AUDIT_LIMIT_MAX))
    ).all()
    return JSONResponse(content=[_audit_item(row) for row in rows])


async def list_positions(request: Request) -> Response:
    """Return every position row.

    Requires ``trades.read`` before the lookup. ``size``, ``cost_native``,
    and ``realized_pnl_native`` are decimal strings. ``updated_at`` is
    ISO-8601. An empty table is an empty list.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    require_permission(
        db,
        user,
        "trades.read",
        entity_type="position",
        entity_id="",
        request_id=request_id_of(request),
    )
    rows = db.scalars(select(Position).order_by(Position.bot_id)).all()
    return JSONResponse(content=[_position_item(row) for row in rows])


async def list_trades(request: Request, limit: int = _TRADE_LIMIT_DEFAULT) -> Response:
    """Return the newest trades.

    Requires ``trades.read`` before the lookup. ``limit`` defaults to 50.
    A limit outside 1..200 is 422 ``invalid_limit``. Money fields are decimal
    strings. ``tx_sig`` is null when the fill has no signature. This route
    does not insert a trade.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    require_permission(
        db,
        user,
        "trades.read",
        entity_type="trade",
        entity_id="",
        request_id=request_id_of(request),
    )
    bounded = _trade_limit(limit)
    if isinstance(bounded, JSONResponse):
        return bounded
    rows = db.scalars(select(Trade).order_by(Trade.ts.desc(), Trade.id.desc()).limit(bounded)).all()
    return JSONResponse(content=[_trade_item(row) for row in rows])


async def list_price_alerts(request: Request) -> Response:
    """Return price alerts.

    Requires ``alerts.read``. ``threshold`` is a JSON string.
    """
    opened = _session_user(request)
    if isinstance(opened, JSONResponse):
        return opened
    db, user = opened
    require_permission(
        db,
        user,
        "alerts.read",
        entity_type="price_alert",
        entity_id="",
        request_id=request_id_of(request),
    )
    rows = db.scalars(select(PriceAlert).order_by(PriceAlert.id)).all()
    return JSONResponse(content=[_price_alert_item(row) for row in rows])


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


def _coin_item(db: Session, coin: Coin) -> dict[str, object]:
    """Serialize one coin and its latest tick, if a tick exists."""
    tick = db.scalar(
        select(Tick)
        .where(Tick.chain == coin.chain, Tick.coin_address == coin.address)
        .order_by(Tick.ts.desc(), Tick.id.desc())
        .limit(1)
    )
    return {
        "chain": coin.chain,
        "address": coin.address,
        "name": coin.name,
        "symbol": coin.symbol,
        "status": coin.status,
        "created_at": _timestamp(coin.created_at),
        "graduated_at": _timestamp(coin.graduated_at),
        "tick": None if tick is None else _tick_item(tick, history=False),
    }


def _tick_item(tick: Tick, *, history: bool) -> dict[str, object]:
    """Serialize tick fields. History rows also include volume and holders."""
    payload: dict[str, object] = {
        "ts": _timestamp(tick.ts),
        "price_native": _decimal(tick.price_native),
        "price_usd": _decimal(tick.price_usd),
        "mcap_usd": _decimal(tick.mcap_usd),
        "liquidity_native": _decimal(tick.liquidity_native),
        "curve_pct": _decimal(tick.curve_pct),
        "virtual_sol_reserves": _decimal(tick.virtual_sol_reserves),
        "virtual_token_reserves": _decimal(tick.virtual_token_reserves),
    }
    if history:
        payload["volume_1m"] = _decimal(tick.volume_1m)
        payload["volume_5m"] = _decimal(tick.volume_5m)
        payload["volume_15m"] = _decimal(tick.volume_15m)
        payload["holders"] = tick.holders
    return payload


def _audit_item(row: AuditEvent) -> dict[str, object]:
    """Serialize one audit row without adding columns."""
    return {
        "id": row.id,
        "ts": _timestamp(row.ts),
        "actor_type": row.actor_type,
        "actor_id": row.actor_id,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "result": row.result,
        "before_json": row.before_json,
        "after_json": row.after_json,
        "request_id": row.request_id,
        "detail": row.detail,
    }


def _position_item(row: Position) -> dict[str, object]:
    """Serialize one position. Money fields are decimal strings."""
    return {
        "bot_id": row.bot_id,
        "chain": row.chain,
        "coin_address": row.coin_address,
        "size": _decimal(row.size),
        "cost_native": _decimal(row.cost_native),
        "realized_pnl_native": _decimal(row.realized_pnl_native),
        "updated_at": _timestamp(row.updated_at),
    }


def _trade_item(row: Trade) -> dict[str, object]:
    """Serialize one fill. Money fields are decimal strings. ``tx_sig`` may be null."""
    return {
        "id": row.id,
        "bot_id": row.bot_id,
        "chain": row.chain,
        "coin_address": row.coin_address,
        "side": row.side,
        "amount_native": _decimal(row.amount_native),
        "price_native": _decimal(row.price_native),
        "price_usd": _decimal(row.price_usd),
        "mcap_usd": _decimal(row.mcap_usd),
        "fee_native": _decimal(row.fee_native),
        "price_impact_pct": _decimal(row.price_impact_pct),
        "tx_sig": row.tx_sig,
        "paper": row.paper,
        "actor_id": row.actor_id,
        "ts": _timestamp(row.ts),
    }


def _trade_limit(limit: int) -> int | JSONResponse:
    """Accept a limit in 1..200, or return 422 ``invalid_limit``."""
    if limit < _TRADE_LIMIT_MIN or limit > _TRADE_LIMIT_MAX:
        return error_response(422, "invalid_limit", "Limit must be from 1 to 200.")
    return limit


def _price_alert_item(row: PriceAlert) -> dict[str, object]:
    """Serialize one price alert. ``threshold`` is a string."""
    return {
        "id": row.id,
        "chain": row.chain,
        "coin_address": row.coin_address,
        "condition": row.condition,
        "threshold": _decimal(row.threshold),
        "enabled": row.enabled,
        "created_by": row.created_by,
    }


def _clamp(value: int, high: int) -> int:
    """Return ``value`` forced into the inclusive range 1..``high``."""
    if value < 1:
        return 1
    if value > high:
        return high
    return value


def _coin_entity_id(chain: str, address: str) -> str:
    """Build an audit entity id that fits the audit column."""
    return f"{chain}:{address}"[:128]


def _decimal(value: Decimal | None) -> str | None:
    """Render a decimal as a JSON string, or null when the column is null."""
    if value is None:
        return None
    return format(value, "f")


def _timestamp(value: datetime | None) -> str | None:
    """Render a UTC datetime as an ISO-8601 string, or null when it is absent."""
    if value is None:
        return None
    return value.isoformat()


router.get("/api/coins")(list_coins)
router.get("/api/ticks")(list_ticks)
router.get("/api/positions")(list_positions)
router.get("/api/trades")(list_trades)
router.get("/api/audit")(list_audit)
router.get("/api/price-alerts")(list_price_alerts)
