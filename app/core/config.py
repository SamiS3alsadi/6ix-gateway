from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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

    @field_validator("database_url")
    @classmethod
    def _force_asyncpg_driver(cls, v: str) -> str:
        """Rewrite bare Postgres URLs to the asyncpg driver.

        Managed hosts (Railway, Heroku, Fly, Supabase) auto-inject a URL
        like `postgresql://…` or `postgres://…`. SQLAlchemy routes those
        through psycopg2 by default — a sync driver — which our async
        engine can't use. Rewriting to `postgresql+asyncpg://…` here means
        the app boots on those platforms without hand-editing the injected
        env var. URLs that already carry an explicit `+driver` suffix
        (e.g. `postgresql+asyncpg://`, `postgresql+psycopg2://`) are left
        untouched; likewise non-Postgres URLs like `sqlite+aiosqlite://`.
        """
        if v.startswith("postgresql+"):
            return v
        if v.startswith("postgresql://"):
            return "postgresql+asyncpg://" + v[len("postgresql://"):]
        if v.startswith("postgres://"):
            return "postgresql+asyncpg://" + v[len("postgres://"):]
        return v

    # Stripe
    stripe_api_key: str = Field(default="sk_test_placeholder")
    stripe_webhook_secret: str = Field(default="whsec_placeholder")
    stripe_publishable_key: str = Field(default="pk_test_placeholder")

    # Security
    secret_key: str = Field(default="dev-only-change-me")
    admin_api_key: str = Field(default="dev-only-admin-key")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def stripe_webhook_secret_prefix(self) -> str:
        """First 10 chars of the webhook secret for startup diagnostics.

        Used only for logging — never log or expose the full value. A real
        Stripe webhook secret always starts with `whsec_` so the prefix is
        enough to confirm it's loading from the right env var without
        revealing key material.
        """
        s = self.stripe_webhook_secret or ""
        return f"{s[:10]}...(len={len(s)})" if s else "<empty>"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
