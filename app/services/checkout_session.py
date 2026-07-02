"""Hosted checkout session service.

Session ↔ PaymentIntent lifecycle:

  create_session(merchant, payload)
    → payment_service.create_intent(payload, merchant=merchant)   [eager PI]
    → CheckoutSession row referencing that PI              [OPEN, 24h TTL]

  customer opens /checkout/{cs_id}
    → get_open_session(id): fetches the row, transitions OPEN→EXPIRED if
      past expires_at. Caller inspects .status to decide what to render.

  stripe payment_intent.succeeded webhook
    → find_by_payment_intent(pi_id) → complete_session(cs_id):
      OPEN/EXPIRED → COMPLETED. Idempotent — replaying webhooks is safe.

Idempotency-key semantics: the session inherits the PaymentIntent's key
via `payment_service.create_intent`. A replayed create with the same key
returns the same PI, and we look up the existing session by that PI id.
No dedicated `idempotency_key` column on CheckoutSession itself.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CheckoutSessionNotFoundError
from app.models.checkout_session import CheckoutSession, CheckoutSessionStatus
from app.models.merchant import Merchant
from app.schemas.checkout_session import CheckoutSessionCreate
from app.schemas.payment import PaymentIntentCreate
from app.services import payment as payment_service


async def get_by_id(
    session: AsyncSession, session_id: str
) -> CheckoutSession | None:
    return await session.get(CheckoutSession, session_id)


async def find_by_payment_intent(
    session: AsyncSession, payment_intent_id: str
) -> CheckoutSession | None:
    """Reverse lookup used by the webhook dispatcher."""
    result = await session.execute(
        select(CheckoutSession).where(
            CheckoutSession.payment_intent_id == payment_intent_id
        )
    )
    return result.scalar_one_or_none()


async def create_session(
    session: AsyncSession,
    *,
    merchant: Merchant,
    payload: CheckoutSessionCreate,
) -> CheckoutSession:
    """Create a checkout session + its underlying PaymentIntent.

    Idempotent: replaying with the same idempotency_key returns the same
    session because payment_service.create_intent returns the same PI, and
    we then find the CheckoutSession already linked to it.
    """
    intent = await payment_service.create_intent(
        session,
        PaymentIntentCreate(
            amount=payload.amount,
            currency=payload.currency,
            idempotency_key=payload.idempotency_key,
            description=payload.description,
        ),
        merchant=merchant,
    )

    existing = await find_by_payment_intent(session, intent.id)
    if existing is not None:
        return existing

    cs = CheckoutSession(
        merchant_id=merchant.id,
        payment_intent_id=intent.id,
        amount=payload.amount,
        currency=payload.currency.lower(),
        description=payload.description,
        success_url=payload.success_url,
        status=CheckoutSessionStatus.OPEN,
    )
    session.add(cs)
    await session.commit()
    await session.refresh(cs)
    return cs


async def get_open_session(
    session: AsyncSession, session_id: str
) -> CheckoutSession:
    """Fetch a session by id, transitioning OPEN → EXPIRED if past its TTL.

    Always returns the row (or raises CheckoutSessionNotFoundError). The
    caller inspects `.status` to decide what to render — an expired or
    completed session still round-trips, it just isn't payable.
    """
    cs = await get_by_id(session, session_id)
    if cs is None:
        raise CheckoutSessionNotFoundError(
            detail=f"checkout session {session_id} not found"
        )
    # SQLite doesn't have a tz-aware timestamp type — it round-trips as
    # naive UTC. Postgres returns aware. Normalize both sides so the
    # comparison works regardless of backend.
    expires_at = cs.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        cs.status == CheckoutSessionStatus.OPEN
        and expires_at < datetime.now(timezone.utc)
    ):
        cs.status = CheckoutSessionStatus.EXPIRED
        await session.commit()
        await session.refresh(cs)
    return cs


async def complete_session(
    session: AsyncSession, session_id: str
) -> CheckoutSession:
    """Mark a session COMPLETED. Called by the webhook when the underlying
    PaymentIntent succeeds. Idempotent — replaying is a no-op.

    We deliberately promote EXPIRED sessions to COMPLETED too: if the
    customer's payment landed after the TTL but succeeded on Stripe's
    side, the money moved, so the session did complete.
    """
    cs = await get_by_id(session, session_id)
    if cs is None:
        raise CheckoutSessionNotFoundError(
            detail=f"checkout session {session_id} not found"
        )
    if cs.status != CheckoutSessionStatus.COMPLETED:
        cs.status = CheckoutSessionStatus.COMPLETED
        await session.commit()
        await session.refresh(cs)
    return cs
