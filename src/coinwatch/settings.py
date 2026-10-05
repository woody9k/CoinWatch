"""Process settings loaded from the environment."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the API and engine processes.

    Environment variables and an optional ``.env`` file supply values.
    ``DATABASE_URL`` is the SQLAlchemy URL for the shared SQLite database.
    ``COINWATCH_ADMIN_USER`` and ``COINWATCH_ADMIN_PASSWORD`` seed the first
    admin. The password is a bootstrap secret: do not log it.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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


def get_settings() -> Settings:
    """Load settings from the current environment."""
    return Settings()
