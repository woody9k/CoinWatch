"""Process settings loaded from the environment."""

from decimal import Decimal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the API and engine processes.

    Environment variables and an optional ``.env`` file supply values.
    ``DATABASE_URL`` is the SQLAlchemy URL for the shared SQLite database.
    ``COINWATCH_ADMIN_USER`` and ``COINWATCH_ADMIN_PASSWORD`` seed the first
    admin. The password is a bootstrap secret: do not log it.
    ``SOLANA_RPC_URL`` and ``COINWATCH_SOL_USD`` enable the BobCoin poller.
    An empty SOL price stays unset so the process does not invent one.
    ``SOLANA_RPC_URL`` may contain an API key: do not log it.
    ``COINWATCH_KILL_SWITCH`` refuses new quotes when true.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str = Field(
        default="sqlite:///data/coinwatch.db",
        description="SQLAlchemy URL for the CoinWatch database.",
    )
    coinwatch_admin_user: str = Field(
        default="",
        description="Bootstrap admin username (COINWATCH_ADMIN_USER). Empty skips seeding.",
    )
    coinwatch_admin_password: SecretStr = Field(
        default=SecretStr(""),
        description="Bootstrap admin password (COINWATCH_ADMIN_PASSWORD). Never log this value.",
    )
    solana_rpc_url: str = Field(
        default="",
        description="Solana JSON-RPC URL (SOLANA_RPC_URL). Empty disables the coin poller.",
    )
    sol_usd: Decimal | None = Field(
        default=None,
        validation_alias="COINWATCH_SOL_USD",
        description=(
            "SOL price in USD (COINWATCH_SOL_USD). Empty means unset. "
            "The poller does not invent a price."
        ),
    )
    coinwatch_kill_switch: bool = Field(
        default=False,
        description="When true, new quotes are refused (COINWATCH_KILL_SWITCH).",
    )

    @field_validator("solana_rpc_url", mode="before")
    @classmethod
    def _strip_rpc_url(cls, value: object) -> object:
        """Trim whitespace so a blank RPC URL stays disabled."""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("sol_usd", mode="before")
    @classmethod
    def _blank_sol_usd_is_unset(cls, value: object) -> object:
        """Treat a blank SOL price as unset instead of guessing a rate."""
        if isinstance(value, str) and value.strip() == "":
            return None
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("coinwatch_kill_switch", mode="before")
    @classmethod
    def _blank_kill_switch_is_off(cls, value: object) -> object:
        """Treat a blank kill-switch value as off."""
        if isinstance(value, str) and value.strip() == "":
            return False
        if isinstance(value, str):
            return value.strip()
        return value


def get_settings() -> Settings:
    """Load settings from the current environment."""
    return Settings()
