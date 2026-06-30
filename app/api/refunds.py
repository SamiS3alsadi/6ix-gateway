from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.schemas.payment import PaymentIntentRead, RefundCreate
from app.services import payment as payment_service

# Path is /payments/intents/{intent_id}/refund — the prefix lives on the route,
# not the router, so the refund endpoint nests under the payments resource.
router = APIRouter(tags=["refunds"])


@router.post(
    "/payments/intents/{intent_id}/refund",
    response_model=PaymentIntentRead,
    status_code=status.HTTP_201_CREATED,
)
async def refund_payment_intent(
    intent_id: str,
    payload: RefundCreate,
    db: AsyncSession = Depends(get_db),
) -> PaymentIntentRead:
    intent = await payment_service.refund_intent(
        db,
        intent_id,
        idempotency_key=payload.idempotency_key,
        amount=payload.amount,
        reason=payload.reason,
    )
    return PaymentIntentRead.model_validate(intent)
