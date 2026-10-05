"""Paper wallet, strategy, and bot controls.

The caller owns the transaction. These functions flush the domain row and
its audit row together, then return. They do not commit. Live bots are
refused. Private keys are not accepted or stored.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from coinwatch.db.models import Bot, Chain, Coin, Strategy, User, Wallet
from coinwatch.errors import RequestRejected, StrategyError
from coinwatch.services.audit import record_audit
from coinwatch.services.identity import require_permission
from coinwatch.strategy import load_strategy

_SECRET_MARKERS = ("password", "secret", "seed", "private", "api_key")
_CHAIN_LEN = 32
_LABEL_LEN = 128
_ADDRESS_LEN = 128
_NAME_LEN = 128
_TRANSITIONS: dict[str, dict[str, str]] = {
    "start": {"paused": "running", "stopped": "running"},
    "pause": {"running": "paused"},
    "stop": {"running": "stopped", "paused": "stopped"},
}


class UnreadableBody:
    """Sentinel for a request body that is not JSON."""


UNREADABLE_BODY = UnreadableBody()


def create_wallet(
    session: Session,
    actor: User,
    body: object,
    *,
    request_id: str,
) -> Wallet:
    """Insert a public wallet row and audit ``wallet.create``.

    Requires ``wallets.manage``. ``chain`` must already exist. A body key
    whose name includes ``password``, ``secret``, ``seed``, ``private``, or
    ``api_key`` is rejected and nothing is stored. The audit snapshot is
    ``id``, ``chain``, and ``label`` only. Does not commit.
    """
    require_permission(
        session,
        actor,
        "wallets.manage",
        entity_type="wallet",
        entity_id="",
        request_id=request_id,
    )
    payload = _mapping(body)
    if _has_secret_key(payload):
        raise RequestRejected("invalid_request", "Invalid request.")
    chain = _text(payload, "chain", _CHAIN_LEN)
    label = _text(payload, "label", _LABEL_LEN)
    public_address = _text(payload, "public_address", _ADDRESS_LEN)
    if session.get(Chain, chain) is None:
        raise RequestRejected("invalid_request", "Chain does not exist.")
    now = datetime.now(UTC)
    wallet = Wallet(
        chain=chain,
        label=label,
        public_address=public_address,
        created_by=actor.id,
        created_at=now,
    )
    session.add(wallet)
    session.flush()
    record_audit(
        session,
        actor_type="user",
        actor_id=str(actor.id),
        action="wallet.create",
        entity_type="wallet",
        entity_id=str(wallet.id),
        result="ok",
        after={"id": wallet.id, "chain": wallet.chain, "label": wallet.label},
        request_id=request_id,
    )
    return wallet


def list_wallets(session: Session, actor: User, *, request_id: str) -> list[Wallet]:
    """Return public wallets in id order.

    Requires ``wallets.read`` before the query. Does not change rows. The
    stored columns are the public address and label; key material is not a
    column on ``wallets``.
    """
    require_permission(
        session,
        actor,
        "wallets.read",
        entity_type="wallet",
        entity_id="",
        request_id=request_id,
    )
    return list(session.scalars(select(Wallet).order_by(Wallet.id)).all())


def create_strategy(
    session: Session,
    actor: User,
    body: object,
    *,
    request_id: str,
) -> Strategy:
    """Insert a strategy when ``load_strategy`` accepts the YAML.

    Requires ``strategies.write``. A document ``load_strategy`` rejects is
    ``invalid_strategy`` and nothing is stored. The audit snapshot is ``id``
    and ``name`` only. Does not commit.
    """
    require_permission(
        session,
        actor,
        "strategies.write",
        entity_type="strategy",
        entity_id="",
        request_id=request_id,
    )
    payload = _mapping(body)
    name = _text(payload, "name", _NAME_LEN)
    yaml_config = payload.get("yaml_config")
    if not isinstance(yaml_config, str) or yaml_config == "":
        raise RequestRejected("invalid_request", "Invalid request.")
    try:
        load_strategy(yaml_config)
    except StrategyError as exc:
        raise RequestRejected("invalid_strategy", str(exc)) from exc
    now = datetime.now(UTC)
    strategy = Strategy(
        name=name,
        yaml_config=yaml_config,
        created_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    session.add(strategy)
    session.flush()
    record_audit(
        session,
        actor_type="user",
        actor_id=str(actor.id),
        action="strategy.create",
        entity_type="strategy",
        entity_id=str(strategy.id),
        result="ok",
        after={"id": strategy.id, "name": strategy.name},
        request_id=request_id,
    )
    return strategy


def list_strategies(session: Session, actor: User, *, request_id: str) -> list[Strategy]:
    """Return strategies in id order.

    Requires ``strategies.read``. Does not change rows.
    """
    require_permission(
        session,
        actor,
        "strategies.read",
        entity_type="strategy",
        entity_id="",
        request_id=request_id,
    )
    return list(session.scalars(select(Strategy).order_by(Strategy.id)).all())


def create_bot(
    session: Session,
    actor: User,
    body: object,
    *,
    request_id: str,
) -> Bot:
    """Insert a paused paper bot and audit ``bot.create``.

    Requires ``bots.control``. ``paper`` must be omitted or true. ``false``
    is ``live_disabled`` and inserts nothing. The coin, strategy, and wallet
    must exist, and the wallet chain must match. ``act_on_inference`` is
    false. Does not commit.
    """
    require_permission(
        session,
        actor,
        "bots.control",
        entity_type="bot",
        entity_id="",
        request_id=request_id,
    )
    payload = _mapping(body)
    _reject_live(payload)
    chain = _text(payload, "chain", _CHAIN_LEN)
    coin_address = _text(payload, "coin_address", _ADDRESS_LEN)
    strategy_id = _identifier(payload, "strategy_id")
    wallet_id = _identifier(payload, "wallet_id")
    if session.get(Coin, (chain, coin_address)) is None:
        raise RequestRejected("invalid_request", "Coin, strategy, or wallet was not found.")
    if session.get(Strategy, strategy_id) is None:
        raise RequestRejected("invalid_request", "Coin, strategy, or wallet was not found.")
    wallet = session.get(Wallet, wallet_id)
    if wallet is None or wallet.chain != chain:
        raise RequestRejected("invalid_request", "Coin, strategy, or wallet was not found.")
    now = datetime.now(UTC)
    bot = Bot(
        chain=chain,
        coin_address=coin_address,
        strategy_id=strategy_id,
        wallet_id=wallet_id,
        status="paused",
        paper=True,
        act_on_inference=False,
        created_by=actor.id,
        created_at=now,
    )
    session.add(bot)
    session.flush()
    record_audit(
        session,
        actor_type="user",
        actor_id=str(actor.id),
        action="bot.create",
        entity_type="bot",
        entity_id=str(bot.id),
        result="ok",
        after={
            "id": bot.id,
            "chain": bot.chain,
            "coin_address": bot.coin_address,
            "status": "paused",
            "paper": True,
        },
        request_id=request_id,
    )
    return bot


def list_bots(session: Session, actor: User, *, request_id: str) -> list[Bot]:
    """Return bots in id order.

    Requires ``bots.read``. Does not change rows.
    """
    require_permission(
        session,
        actor,
        "bots.read",
        entity_type="bot",
        entity_id="",
        request_id=request_id,
    )
    return list(session.scalars(select(Bot).order_by(Bot.id)).all())


def transition_bot(
    session: Session,
    actor: User,
    bot_id: int,
    action: str,
    *,
    request_id: str,
) -> Bot | None:
    """Apply ``start``, ``pause``, or ``stop`` and audit the status change.

    Requires ``bots.control`` before the lookup. ``start`` moves paused or
    stopped to running. ``pause`` moves running to paused. ``stop`` moves
    running or paused to stopped. Any other transition raises
    ``invalid_status`` and leaves the row unchanged. A missing bot returns
    ``None``. Does not commit.
    """
    require_permission(
        session,
        actor,
        "bots.control",
        entity_type="bot",
        entity_id=str(bot_id),
        request_id=request_id,
    )
    bot = session.get(Bot, bot_id)
    if bot is None:
        return None
    allowed = _TRANSITIONS.get(action)
    if allowed is None:
        raise RequestRejected("invalid_status", "That status change is not allowed.")
    target = allowed.get(bot.status)
    if target is None:
        raise RequestRejected("invalid_status", "That status change is not allowed.")
    previous = bot.status
    bot.status = target
    record_audit(
        session,
        actor_type="user",
        actor_id=str(actor.id),
        action=f"bot.{action}",
        entity_type="bot",
        entity_id=str(bot.id),
        result="ok",
        before={"status": previous},
        after={"status": target},
        request_id=request_id,
    )
    return bot


def _mapping(body: object) -> dict[str, object]:
    """Return a string-keyed mapping or reject a body that is not one."""
    if isinstance(body, UnreadableBody) or not isinstance(body, dict):
        raise RequestRejected("invalid_request", "Invalid request.")
    payload: dict[str, object] = {}
    for key, value in body.items():
        if not isinstance(key, str):
            raise RequestRejected("invalid_request", "Invalid request.")
        payload[key] = value
    return payload


def _has_secret_key(value: object) -> bool:
    """Return whether any mapping key name looks like key material."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or _is_secret_name(key) or _has_secret_key(item):
                return True
        return False
    if isinstance(value, list):
        return any(_has_secret_key(item) for item in value)
    return False


def _is_secret_name(key: str) -> bool:
    """Return whether ``key`` includes a secret marker."""
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


def _text(payload: dict[str, object], key: str, limit: int) -> str:
    """Return a non-empty string field that fits its column."""
    value = payload.get(key)
    if not isinstance(value, str) or value == "" or len(value) > limit:
        raise RequestRejected("invalid_request", "Invalid request.")
    return value


def _identifier(payload: dict[str, object], key: str) -> int:
    """Return a positive integer id. Booleans are rejected."""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RequestRejected("invalid_request", "Invalid request.")
    return value


def _reject_live(payload: dict[str, object]) -> None:
    """Refuse ``paper: false`` before any bot row is inserted."""
    if "paper" not in payload:
        return
    paper = payload["paper"]
    if paper is False:
        raise RequestRejected("live_disabled", "Live bots cannot be created.")
    if paper is not True:
        raise RequestRejected("invalid_request", "Invalid request.")
