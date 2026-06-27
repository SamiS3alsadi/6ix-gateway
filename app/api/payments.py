from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
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
    db: AsyncSession = Depends(get_db),
) -> PaymentIntentRead:
    try:
        intent = await payment_service.create_intent(db, payload)
    except payment_service.IdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return PaymentIntentRead.model_validate(intent)


@router.post("/intents/{intent_id}/confirm", response_model=PaymentIntentRead)
async def confirm_payment_intent(
    intent_id: str,
    payload: PaymentIntentConfirm,
    db: AsyncSession = Depends(get_db),
) -> PaymentIntentRead:
    try:
        intent = await payment_service.confirm_intent(db, intent_id, payload)
    except payment_service.PaymentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return PaymentIntentRead.model_validate(intent)


@router.post("/intents/{intent_id}/cancel", response_model=PaymentIntentRead)
async def cancel_payment_intent(
    intent_id: str,
    payload: PaymentIntentCancel,
    db: AsyncSession = Depends(get_db),
) -> PaymentIntentRead:
    try:
        intent = await payment_service.cancel_intent(db, intent_id, payload)
    except payment_service.PaymentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return PaymentIntentRead.model_validate(intent)


@router.get("/intents/{intent_id}", response_model=PaymentIntentRead)
async def retrieve_payment_intent(
    intent_id: str,
    db: AsyncSession = Depends(get_db),
) -> PaymentIntentRead:
    intent = await payment_service.retrieve_intent(db, intent_id)
    if intent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return PaymentIntentRead.model_validate(intent)
