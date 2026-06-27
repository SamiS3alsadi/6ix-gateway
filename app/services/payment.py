"""Payment intent business logic.

Every mutation is keyed by an idempotency_key — replays return the existing
record instead of double-charging. The same key is forwarded to Stripe so the
underlying API call is also idempotent.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger import LedgerEntryDirection
from app.models.payment_intent import PaymentIntent, PaymentIntentStatus
from app.schemas.payment import (
    PaymentIntentCancel,
    PaymentIntentConfirm,
    PaymentIntentCreate,
)
from app.services.ledger import LedgerLeg, record_transaction
from app.services.stripe_client import stripe_client


class PaymentError(Exception):
    pass


class IdempotencyConflict(PaymentError):
    """An idempotency_key reused with different parameters."""


async def _find_by_idempotency_key(
    session: AsyncSession, key: str
) -> PaymentIntent | None:
    result = await session.execute(
        select(PaymentIntent).where(PaymentIntent.idempotency_key == key)
    )
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, payment_intent_id: str) -> PaymentIntent | None:
    result = await session.execute(
        select(PaymentIntent).where(PaymentIntent.id == payment_intent_id)
    )
    return result.scalar_one_or_none()


async def create_intent(
    session: AsyncSession, payload: PaymentIntentCreate
) -> PaymentIntent:
    existing = await _find_by_idempotency_key(session, payload.idempotency_key)
    if existing is not None:
        if (
            existing.amount != payload.amount
            or existing.currency != payload.currency.lower()
        ):
            raise IdempotencyConflict(
                "idempotency_key reused with different parameters"
            )
        return existing

    stripe_intent = await stripe_client.create_payment_intent(
        amount=payload.amount,
        currency=payload.currency,
        idempotency_key=payload.idempotency_key,
        customer=payload.customer_id,
        description=payload.description,
        metadata=payload.metadata,
    )

    intent = PaymentIntent(
        stripe_payment_intent_id=stripe_intent["id"],
        idempotency_key=payload.idempotency_key,
        amount=payload.amount,
        currency=payload.currency.lower(),
        status=PaymentIntentStatus(stripe_intent["status"]),
        customer_id=payload.customer_id,
        description=payload.description,
        client_secret=stripe_intent.get("client_secret"),
        payment_metadata=payload.metadata,
    )
    session.add(intent)
    await session.flush()

    # Authorisation accrual — booked at create-time as a pending receivable.
    await record_transaction(
        session,
        legs=[
            LedgerLeg(
                account=f"customer_pending:{payload.customer_id or 'anon'}",
                direction=LedgerEntryDirection.DEBIT,
                amount=payload.amount,
            ),
            LedgerLeg(
                account="payments_authorised",
                direction=LedgerEntryDirection.CREDIT,
                amount=payload.amount,
            ),
        ],
        currency=payload.currency,
        payment_intent_id=intent.id,
        description="payment_intent.created",
    )

    await session.commit()
    await session.refresh(intent)
    return intent


async def confirm_intent(
    session: AsyncSession,
    payment_intent_id: str,
    payload: PaymentIntentConfirm,
) -> PaymentIntent:
    intent = await get_by_id(session, payment_intent_id)
    if intent is None:
        raise PaymentError(f"PaymentIntent {payment_intent_id} not found")
    if intent.status in (
        PaymentIntentStatus.SUCCEEDED,
        PaymentIntentStatus.CANCELED,
        PaymentIntentStatus.FAILED,
    ):
        return intent

    if not intent.stripe_payment_intent_id:
        raise PaymentError("PaymentIntent has no Stripe id; cannot confirm")

    stripe_intent = await stripe_client.confirm_payment_intent(
        stripe_payment_intent_id=intent.stripe_payment_intent_id,
        payment_method=payload.payment_method_id,
        idempotency_key=payload.idempotency_key,
    )
    intent.status = PaymentIntentStatus(stripe_intent["status"])
    await session.commit()
    await session.refresh(intent)
    return intent


async def cancel_intent(
    session: AsyncSession,
    payment_intent_id: str,
    payload: PaymentIntentCancel,
) -> PaymentIntent:
    intent = await get_by_id(session, payment_intent_id)
    if intent is None:
        raise PaymentError(f"PaymentIntent {payment_intent_id} not found")
    if intent.status == PaymentIntentStatus.CANCELED:
        return intent
    if intent.status == PaymentIntentStatus.SUCCEEDED:
        raise PaymentError("Cannot cancel a succeeded payment; use refund instead")

    if not intent.stripe_payment_intent_id:
        raise PaymentError("PaymentIntent has no Stripe id; cannot cancel")

    stripe_intent = await stripe_client.cancel_payment_intent(
        stripe_payment_intent_id=intent.stripe_payment_intent_id,
        idempotency_key=payload.idempotency_key,
        cancellation_reason=payload.reason,
    )
    intent.status = PaymentIntentStatus(stripe_intent["status"])

    # Reverse the pending authorisation.
    await record_transaction(
        session,
        legs=[
            LedgerLeg(
                account="payments_authorised",
                direction=LedgerEntryDirection.DEBIT,
                amount=intent.amount,
            ),
            LedgerLeg(
                account=f"customer_pending:{intent.customer_id or 'anon'}",
                direction=LedgerEntryDirection.CREDIT,
                amount=intent.amount,
            ),
        ],
        currency=intent.currency,
        payment_intent_id=intent.id,
        description="payment_intent.canceled",
    )

    await session.commit()
    await session.refresh(intent)
    return intent


async def retrieve_intent(
    session: AsyncSession, payment_intent_id: str
) -> PaymentIntent | None:
    """Local read only. Webhooks are the source of truth — we never poll Stripe."""
    return await get_by_id(session, payment_intent_id)


async def apply_succeeded_event(
    session: AsyncSession,
    *,
    stripe_payment_intent_id: str,
    amount_received: int,
) -> PaymentIntent | None:
    """Webhook handler entry point for payment_intent.succeeded."""
    result = await session.execute(
        select(PaymentIntent).where(
            PaymentIntent.stripe_payment_intent_id == stripe_payment_intent_id
        )
    )
    intent = result.scalar_one_or_none()
    if intent is None:
        return None
    if intent.status == PaymentIntentStatus.SUCCEEDED:
        return intent

    intent.status = PaymentIntentStatus.SUCCEEDED
    intent.amount_received = amount_received

    # Move funds from authorised → captured revenue.
    await record_transaction(
        session,
        legs=[
            LedgerLeg(
                account="payments_authorised",
                direction=LedgerEntryDirection.DEBIT,
                amount=amount_received,
            ),
            LedgerLeg(
                account="payments_captured",
                direction=LedgerEntryDirection.CREDIT,
                amount=amount_received,
            ),
        ],
        currency=intent.currency,
        payment_intent_id=intent.id,
        description="payment_intent.succeeded",
    )
    await session.flush()
    return intent
