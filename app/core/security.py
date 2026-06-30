import hmac
import secrets

from fastapi import Header

from app.core.config import settings
from app.core.errors import UnauthorizedError


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


async def require_admin_api_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> None:
    """Static admin credential check — separate from merchant Bearer auth.

    Used to gate operator-only endpoints (merchant onboarding, key issuance).
    The value comes from the ADMIN_API_KEY env var and is compared with
    `hmac.compare_digest` to defeat timing side channels.
    """
    if not x_admin_key or not constant_time_eq(x_admin_key, settings.admin_api_key):
        raise UnauthorizedError(detail="invalid or missing X-Admin-Key header")


def new_idempotency_key() -> str:
    return secrets.token_urlsafe(24)
