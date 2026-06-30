"""FastAPI auth dependency for the merchant API.

Use as:
    from app.core.auth import require_api_key
    ...
    async def endpoint(merchant: Merchant = Depends(require_api_key)): ...

The dependency reads `Authorization: Bearer <key>`, hands the raw key to
`api_key_service.verify_api_key`, and returns the `Merchant` row on success.
On any failure it raises `UnauthorizedError`, which the global handler in
main.py renders as a structured 401.
"""
from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import UnauthorizedError
from app.models.merchant import Merchant
from app.services import api_key as api_key_service

# auto_error=False so we surface our own structured 401 instead of FastAPI's
# default 403 + non-typed error body.
_bearer_scheme = HTTPBearer(auto_error=False, scheme_name="MerchantAPIKey")


async def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Merchant:
    if credentials is None:
        raise UnauthorizedError(detail="missing Authorization header")
    if credentials.scheme.lower() != "bearer":
        raise UnauthorizedError(
            detail=f"expected Bearer scheme, got {credentials.scheme!r}"
        )
    return await api_key_service.verify_api_key(db, credentials.credentials)
