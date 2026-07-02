"""Merchant API for hosted checkout sessions.

Both routes are Bearer-authed (merchant credential). Cross-merchant lookups
return 404, not 403, so a merchant probing an id they don't own can't
distinguish "belongs to someone else" from "doesn't exist" — same tenant
isolation pattern as the dashboard.
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.db import get_db
from app.core.errors import CheckoutSessionNotFoundError
from app.models.merchant import Merchant
from app.schemas.checkout_session import (
    CheckoutSessionCreate,
    CheckoutSessionRead,
)
from app.services import checkout_session as cs_service

router = APIRouter(prefix="/checkout-sessions", tags=["checkout-sessions"])


@router.post(
    "",
    response_model=CheckoutSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_checkout_session(
    payload: CheckoutSessionCreate,
    merchant: Merchant = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> CheckoutSessionRead:
    cs = await cs_service.create_session(
        db, merchant=merchant, payload=payload
    )
    return CheckoutSessionRead.model_validate(cs)


@router.get("/{session_id}", response_model=CheckoutSessionRead)
async def retrieve_checkout_session(
    session_id: str,
    merchant: Merchant = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> CheckoutSessionRead:
    # get_open_session triggers the OPEN→EXPIRED transition if past TTL,
    # so the returned status is fresh even for stale sessions.
    cs = await cs_service.get_open_session(db, session_id)
    if cs.merchant_id != merchant.id:
        # Uniform 404 for "doesn't exist" and "belongs to another merchant".
        raise CheckoutSessionNotFoundError(
            detail=f"checkout session {session_id} not found"
        )
    return CheckoutSessionRead.model_validate(cs)
