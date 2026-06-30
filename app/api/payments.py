from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.db import get_db
from app.core.errors import PaymentNotFoundError
from app.models.merchant import Merchant
from app.schemas.payment import (
    PaymentIntentCancel,
    PaymentIntentConfirm,
    PaymentIntentCreate,
    PaymentIntentRead,
)
from app.services import payment as payment_service

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post(
    "/intents",
    response_model=PaymentIntentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_intent(
    payload: PaymentIntentCreate,
    merchant: Merchant = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> PaymentIntentRead:
    intent = await payment_service.create_intent(db, payload, merchant=merchant)
    return PaymentIntentRead.model_validate(intent)


@router.post("/intents/{intent_id}/confirm", response_model=PaymentIntentRead)
async def confirm_payment_intent(
    intent_id: str,
    payload: PaymentIntentConfirm,
    merchant: Merchant = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> PaymentIntentRead:
    intent = await payment_service.confirm_intent(db, intent_id, payload)
    return PaymentIntentRead.model_validate(intent)


@router.post("/intents/{intent_id}/cancel", response_model=PaymentIntentRead)
async def cancel_payment_intent(
    intent_id: str,
    payload: PaymentIntentCancel,
    merchant: Merchant = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> PaymentIntentRead:
    intent = await payment_service.cancel_intent(db, intent_id, payload)
    return PaymentIntentRead.model_validate(intent)


@router.get("/intents/{intent_id}", response_model=PaymentIntentRead)
async def retrieve_payment_intent(
    intent_id: str,
    merchant: Merchant = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> PaymentIntentRead:
    intent = await payment_service.retrieve_intent(db, intent_id)
    if intent is None:
        raise PaymentNotFoundError(detail=f"PaymentIntent {intent_id} not found")
    return PaymentIntentRead.model_validate(intent)
