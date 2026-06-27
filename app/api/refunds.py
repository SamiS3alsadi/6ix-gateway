from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.ledger import LedgerEntryDirection
from app.models.payment_intent import PaymentIntentStatus
from app.schemas.payment import RefundCreate
from app.services import payment as payment_service
from app.services.ledger import LedgerLeg, record_transaction
from app.services.stripe_client import stripe_client

router = APIRouter(prefix="/refunds", tags=["refunds"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_refund(
    payload: RefundCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    intent = await payment_service.get_by_id(db, payload.payment_intent_id)
    if intent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="payment intent not found"
        )
    if intent.status != PaymentIntentStatus.SUCCEEDED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payment intent is not in a refundable state",
        )
    if not intent.stripe_payment_intent_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payment intent missing stripe id",
        )

    refundable = intent.amount_received - intent.amount_refunded
    amount = payload.amount or refundable
    if amount <= 0 or amount > refundable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"refund amount must be 1..{refundable}",
        )

    refund = await stripe_client.create_refund(
        stripe_payment_intent_id=intent.stripe_payment_intent_id,
        amount=amount,
        idempotency_key=payload.idempotency_key,
        reason=payload.reason,
    )

    intent.amount_refunded += amount

    await record_transaction(
        db,
        legs=[
            LedgerLeg(
                account="payments_captured",
                direction=LedgerEntryDirection.DEBIT,
                amount=amount,
            ),
            LedgerLeg(
                account="refunds_paid",
                direction=LedgerEntryDirection.CREDIT,
                amount=amount,
            ),
        ],
        currency=intent.currency,
        payment_intent_id=intent.id,
        description=f"refund:{refund['id']}",
    )
    await db.commit()

    return {
        "id": refund["id"],
        "payment_intent_id": intent.id,
        "amount": amount,
        "currency": intent.currency,
        "status": refund.get("status"),
    }
