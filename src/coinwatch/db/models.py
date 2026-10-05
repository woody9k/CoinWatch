"""ORM models for the CoinWatch SQLite schema.

Column sets follow spec section 10. Money and prices use ``Numeric``.
Timestamps are timezone-aware UTC. ``audit_events`` is append-only in
application code: insert the row with the change, and do not update or
delete it.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from coinwatch.db.base import MONEY, Base, UtcDateTime

_CHAIN = String(32)
_ADDRESS = String(128)


class User(Base):
    """Local operator account. The system actor is not a login row."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    disabled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class UserSession(Base):
    """Server-side login session.

    ``id`` is an unguessable token stored in the cookie, not a sequential integer.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Chain(Base):
    """Chain the engine knows how to trade. Solana is seeded."""

    __tablename__ = "chains"

    id: Mapped[str] = mapped_column(_CHAIN, primary_key=True)
    native_symbol: Mapped[str] = mapped_column(String(16))


class Coin(Base):
    """Token tracked on one chain."""

    __tablename__ = "coins"

    chain: Mapped[str] = mapped_column(ForeignKey("chains.id"), primary_key=True)
    address: Mapped[str] = mapped_column(_ADDRESS, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    symbol: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    graduated_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(32))


class Tick(Base):
    """One poll of price, liquidity, and volume for a coin."""

    __tablename__ = "ticks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chain", "coin_address"],
            ["coins.chain", "coins.address"],
        ),
        Index("ix_ticks_chain_coin_address_ts", "chain", "coin_address", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(_CHAIN)
    coin_address: Mapped[str] = mapped_column(_ADDRESS)
    ts: Mapped[datetime] = mapped_column(UtcDateTime)
    price_native: Mapped[Decimal] = mapped_column(MONEY)
    price_usd: Mapped[Decimal] = mapped_column(MONEY)
    mcap_usd: Mapped[Decimal] = mapped_column(MONEY)
    liquidity_native: Mapped[Decimal] = mapped_column(MONEY)
    curve_pct: Mapped[Decimal | None] = mapped_column(MONEY)
    volume_1m: Mapped[Decimal] = mapped_column(MONEY)
    volume_5m: Mapped[Decimal] = mapped_column(MONEY)
    volume_15m: Mapped[Decimal] = mapped_column(MONEY)
    holders: Mapped[int | None]


class Wallet(Base):
    """Public wallet record. Key material stays in the environment."""

    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(ForeignKey("chains.id"))
    label: Mapped[str] = mapped_column(String(128))
    public_address: Mapped[str] = mapped_column(_ADDRESS)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Strategy(Base):
    """Named YAML strategy document."""

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    yaml_config: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Bot(Base):
    """Strategy bound to one coin and one wallet."""

    __tablename__ = "bots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chain", "coin_address"],
            ["coins.chain", "coins.address"],
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(_CHAIN)
    coin_address: Mapped[str] = mapped_column(_ADDRESS)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"))
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"))
    status: Mapped[str] = mapped_column(String(32))
    paper: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    act_on_inference: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Position(Base):
    """Latest position for a bot. Updated in the same transaction as a trade."""

    __tablename__ = "positions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chain", "coin_address"],
            ["coins.chain", "coins.address"],
        ),
    )

    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), primary_key=True)
    chain: Mapped[str] = mapped_column(_CHAIN)
    coin_address: Mapped[str] = mapped_column(_ADDRESS)
    size: Mapped[Decimal] = mapped_column(MONEY)
    cost_native: Mapped[Decimal] = mapped_column(MONEY)
    realized_pnl_native: Mapped[Decimal] = mapped_column(MONEY)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Trade(Base):
    """Paper or live fill. ``actor_id`` is a user id or ``system``."""

    __tablename__ = "trades"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chain", "coin_address"],
            ["coins.chain", "coins.address"],
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"))
    chain: Mapped[str] = mapped_column(_CHAIN)
    coin_address: Mapped[str] = mapped_column(_ADDRESS)
    side: Mapped[str] = mapped_column(String(16))
    amount_native: Mapped[Decimal] = mapped_column(MONEY)
    price_native: Mapped[Decimal] = mapped_column(MONEY)
    price_usd: Mapped[Decimal] = mapped_column(MONEY)
    mcap_usd: Mapped[Decimal] = mapped_column(MONEY)
    fee_native: Mapped[Decimal] = mapped_column(MONEY)
    price_impact_pct: Mapped[Decimal] = mapped_column(MONEY)
    tx_sig: Mapped[str | None] = mapped_column(String(128))
    paper: Mapped[bool] = mapped_column(Boolean)
    actor_id: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(UtcDateTime)


class PriceAlert(Base):
    """Threshold watched for a coin."""

    __tablename__ = "price_alerts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chain", "coin_address"],
            ["coins.chain", "coins.address"],
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(_CHAIN)
    coin_address: Mapped[str] = mapped_column(_ADDRESS)
    condition: Mapped[str] = mapped_column(String(255))
    threshold: Mapped[Decimal] = mapped_column(MONEY)
    enabled: Mapped[bool] = mapped_column(Boolean)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class Decision(Base):
    """High-volume strategy evaluation that did not become an audit event."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"))
    ts: Mapped[datetime] = mapped_column(UtcDateTime)
    rule: Mapped[str] = mapped_column(String(255))
    outcome: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text)


class Alert(Base):
    """Outbound alert. ``coin_address`` is empty for account-level messages."""

    __tablename__ = "alerts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chain", "coin_address"],
            ["coins.chain", "coins.address"],
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(ForeignKey("chains.id"))
    coin_address: Mapped[str | None] = mapped_column(_ADDRESS)
    type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class Digest(Base):
    """Generated daily digest and the model that wrote it."""

    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime)
    content: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class InferenceCall(Base):
    """One inference task attempt. Prompts are not stored here."""

    __tablename__ = "inference_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    task: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    actor_id: Mapped[str] = mapped_column(String(64))
    latency_ms: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(UtcDateTime)


class AuditEvent(Base):
    """Append-only audit row.

    Insert this in the same transaction as the domain change. Application
    code does not update or delete audit rows and does not expose an update API.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(128))
    result: Mapped[str] = mapped_column(String(16))
    before_json: Mapped[dict[str, object]] = mapped_column(JSON)
    after_json: Mapped[dict[str, object]] = mapped_column(JSON)
    request_id: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text)
