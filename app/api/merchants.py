"""Merchant onboarding + API key management.

All endpoints here are gated by the static ADMIN_API_KEY (X-Admin-Key header)
— they are operator-only. Merchant-facing payment endpoints use Bearer tokens
issued by these endpoints.
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import require_admin_api_key
from app.schemas.merchant import (
    APIKeyCreateRequest,
    APIKeyIssued,
    APIKeyRead,
    MerchantCreate,
    MerchantRead,
)
from app.services import api_key as api_key_service
from app.services import merchant as merchant_service

router = APIRouter(
    prefix="/merchants",
    tags=["merchants"],
    dependencies=[Depends(require_admin_api_key)],
)


@router.post(
    "",
    response_model=MerchantRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_merchant(
    payload: MerchantCreate,
    db: AsyncSession = Depends(get_db),
) -> MerchantRead:
    merchant = await merchant_service.create_merchant(
        db, name=payload.name, email=payload.email
    )
    return MerchantRead.model_validate(merchant)


@router.post(
    "/{merchant_id}/api-keys",
    response_model=APIKeyIssued,
    status_code=status.HTTP_201_CREATED,
)
async def issue_api_key(
    merchant_id: str,
    payload: APIKeyCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> APIKeyIssued:
    """Issue a new key for `merchant_id`. The plaintext is returned exactly
    once in `key` — it can never be recovered from the server later."""
    await merchant_service.get_merchant(db, merchant_id)  # 404 if missing
    row, raw = await api_key_service.create_api_key(
        db, merchant_id=merchant_id, name=payload.name
    )
    return APIKeyIssued(
        **APIKeyRead.model_validate(row).model_dump(),
        key=raw,
    )


@router.get(
    "/{merchant_id}/api-keys",
    response_model=list[APIKeyRead],
)
async def list_api_keys(
    merchant_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[APIKeyRead]:
    await merchant_service.get_merchant(db, merchant_id)
    keys = await merchant_service.list_api_keys(db, merchant_id=merchant_id)
    return [APIKeyRead.model_validate(k) for k in keys]


@router.delete(
    "/{merchant_id}/api-keys/{key_id}",
    response_model=APIKeyRead,
)
async def revoke_api_key(
    merchant_id: str,
    key_id: str,
    db: AsyncSession = Depends(get_db),
) -> APIKeyRead:
    """Soft-revoke (is_active=False). Idempotent — revoking an already-revoked
    key returns the row unchanged."""
    row = await api_key_service.revoke_api_key(
        db, key_id=key_id, merchant_id=merchant_id
    )
    return APIKeyRead.model_validate(row)
