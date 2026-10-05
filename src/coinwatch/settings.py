"""Process settings loaded from the environment."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the API and engine processes.

    Environment variables and an optional ``.env`` file supply values.
    ``DATABASE_URL`` is the SQLAlchemy URL for the shared SQLite database.
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


def get_settings() -> Settings:
    """Load settings from the current environment."""
    return Settings()
