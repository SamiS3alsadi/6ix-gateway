from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: Literal["development", "staging", "production", "test"] = "development"
    app_name: str = "6ix-gateway"
    log_level: str = "INFO"

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://gateway:gateway@localhost:5432/gateway",
        description="Async SQLAlchemy URL — must use the asyncpg driver.",
    )

    # Stripe
    stripe_api_key: str = Field(default="sk_test_placeholder")
    stripe_webhook_secret: str = Field(default="whsec_placeholder")
    stripe_publishable_key: str = Field(default="pk_test_placeholder")

    # Security
    secret_key: str = Field(default="dev-only-change-me")
    dashboard_api_key: str = Field(default="dev-only-dashboard-key")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
