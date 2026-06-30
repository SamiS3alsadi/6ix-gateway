"""Payment intent business logic.

Every mutation is keyed by an idempotency_key — replays return the existing
record instead of double-charging. The same key is forwarded to Stripe so the
underlying API call is also idempotent.
"""
from __future__ import annotations

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    IdempotencyConflictError,
    InvalidAmountError,
    InvalidStateError,
    PaymentNotFoundError,
    RefundFailedError,
    RefundNotAllowedError,
)
from app.models.ledger import LedgerEntryDirection
from app.models.merchant import Merchant
from app.models.payment_intent import PaymentIntent, PaymentIntentStatus
from app.schemas.payment import (
    PaymentIntentCancel,
    PaymentIntentConfirm,
    PaymentIntentCreate,
)
from app.services.ledger import LedgerLeg, record_transaction
from app.services.stripe_client import stripe_client


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
    session: AsyncSession,
    payload: PaymentIntentCreate,
    *,
    merchant: Merchant | None = None,
) -> PaymentIntent:
    """Create or idempotently retrieve a PaymentIntent.

    `merchant` is the authenticated principal — its id is stamped onto the
    new row so every intent is tied back to its issuer for scoping/audit.
    Optional only because internal call sites (workers, backfills) may have
    no merchant context; the public API always passes one.
    """
    existing = await _find_by_idempotency_key(session, payload.idempotency_key)
    if existing is not None:
        if (
            existing.amount != payload.amount
            or existing.currency != payload.currency.lower()
        ):
            raise IdempotencyConflictError(
                detail=(
                    f"idempotency_key={payload.idempotency_key} previously "
                    f"used for amount={existing.amount} currency={existing.currency}; "
                    f"new request has amount={payload.amount} currency={payload.currency.lower()}"
                )
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
        merchant_id=merchant.id if merchant is not None else None,
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
        raise PaymentNotFoundError(
            detail=f"PaymentIntent {payment_intent_id} not found"
        )
    if intent.status in (
        PaymentIntentStatus.SUCCEEDED,
        PaymentIntentStatus.CANCELED,
        PaymentIntentStatus.FAILED,
    ):
        return intent

    if not intent.stripe_payment_intent_id:
        raise InvalidStateError(
            detail=f"PaymentIntent {payment_intent_id} has no Stripe id; cannot confirm"
        )

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
        raise PaymentNotFoundError(
            detail=f"PaymentIntent {payment_intent_id} not found"
        )
    if intent.status == PaymentIntentStatus.CANCELED:
        return intent
    if intent.status == PaymentIntentStatus.SUCCEEDED:
        raise InvalidStateError(
            detail=(
                f"PaymentIntent {payment_intent_id} is succeeded; "
                "use refund instead of cancel"
            )
        )

    if not intent.stripe_payment_intent_id:
        raise InvalidStateError(
            detail=f"PaymentIntent {payment_intent_id} has no Stripe id; cannot cancel"
        )

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


async def refund_intent(
    session: AsyncSession,
    payment_intent_id: str,
    *,
    idempotency_key: str,
    amount: int | None = None,
    reason: str | None = None,
) -> PaymentIntent:
    """Refund a succeeded payment intent — partial or full.

    Defaults to a full refund of the remaining (non-refunded) captured amount.
    Writes a double-entry pair: debit captured revenue, credit the customer.
    Updates `amount_refunded` on the intent and forwards the idempotency_key
    to Stripe so a retry hits the same Stripe refund object.
    """
    intent = await get_by_id(session, payment_intent_id)
    if intent is None:
        raise PaymentNotFoundError(
            detail=f"PaymentIntent {payment_intent_id} not found"
        )
    if intent.status != PaymentIntentStatus.SUCCEEDED:
        raise RefundNotAllowedError(
            detail=(
                f"PaymentIntent {payment_intent_id} is {intent.status.value}; "
                "only succeeded intents are refundable"
            )
        )
    if not intent.stripe_payment_intent_id:
        raise RefundNotAllowedError(
            detail=f"PaymentIntent {payment_intent_id} has no Stripe id"
        )

    refundable = intent.amount_received - intent.amount_refunded
    if refundable <= 0:
        raise InvalidAmountError(
            detail=f"PaymentIntent {payment_intent_id} is already fully refunded"
        )

    refund_amount = amount if amount is not None else refundable
    if refund_amount <= 0 or refund_amount > refundable:
        raise InvalidAmountError(
            detail=f"refund amount {refund_amount} outside 1..{refundable}"
        )

    try:
        refund = await stripe_client.create_refund(
            stripe_payment_intent_id=intent.stripe_payment_intent_id,
            amount=refund_amount,
            idempotency_key=idempotency_key,
            reason=reason,
        )
    except stripe.StripeError as exc:
        raise RefundFailedError(detail=str(exc)) from exc

    intent.amount_refunded += refund_amount

    customer_account = f"customer:{intent.customer_id or 'anon'}"
    await record_transaction(
        session,
        legs=[
            LedgerLeg(
                account="payments_captured",
                direction=LedgerEntryDirection.DEBIT,
                amount=refund_amount,
            ),
            LedgerLeg(
                account=customer_account,
                direction=LedgerEntryDirection.CREDIT,
                amount=refund_amount,
            ),
        ],
        currency=intent.currency,
        payment_intent_id=intent.id,
        description=f"refund:{refund['id']}",
        metadata={"stripe_refund_id": refund["id"], "reason": reason},
    )

    await session.commit()
    await session.refresh(intent)
    return intent
