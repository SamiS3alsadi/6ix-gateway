import hmac
import secrets

from fastapi import Header, HTTPException, status

from app.core.config import settings


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


async def require_dashboard_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Simple bearer-style key check for the internal dashboard router."""
    if not x_api_key or not constant_time_eq(x_api_key, settings.dashboard_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


def new_idempotency_key() -> str:
    return secrets.token_urlsafe(24)
