"""Merchant onboarding + API key inventory."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    IdempotencyConflictError,
    MerchantNotFoundError,
)
from app.models.api_key import APIKey
from app.models.merchant import Merchant


async def create_merchant(
    session: AsyncSession, *, name: str, email: str
) -> Merchant:
    """Insert a new merchant. Raises IdempotencyConflictError on duplicate email."""
    merchant = Merchant(name=name, email=email.lower())
    session.add(merchant)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise IdempotencyConflictError(
            detail=f"merchant with email {email!r} already exists"
        ) from exc
    await session.refresh(merchant)
    return merchant


async def get_merchant(
    session: AsyncSession, merchant_id: str
) -> Merchant:
    """Return a merchant or raise MerchantNotFoundError."""
    merchant = await session.get(Merchant, merchant_id)
    if merchant is None:
        raise MerchantNotFoundError(detail=f"merchant {merchant_id} not found")
    return merchant


async def list_api_keys(
    session: AsyncSession, *, merchant_id: str
) -> list[APIKey]:
    """All keys for a merchant, newest first. Includes revoked ones — the
    UI/operator usually wants the audit trail."""
    result = await session.execute(
        select(APIKey)
        .where(APIKey.merchant_id == merchant_id)
        .order_by(APIKey.created_at.desc())
    )
    return list(result.scalars().all())
