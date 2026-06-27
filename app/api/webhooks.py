import logging
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.models.webhook_event import WebhookEvent
from app.services import payment as payment_service
from app.services.stripe_client import stripe_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not stripe_signature:
        logger.warning("webhook rejected: missing Stripe-Signature header")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header",
        )

    # IMPORTANT: signature verification requires the *raw* request bytes.
    # request.body() returns the unparsed body — never replace this with a
    # Pydantic body param or json() parse, which would mutate whitespace and
    # break the HMAC.
    payload = await request.body()
    logger.debug(
        "webhook received: body_bytes=%d sig_header_prefix=%s",
        len(payload),
        stripe_signature[:20],
    )

    try:
        event = stripe_client.construct_webhook_event(
            payload=payload, sig_header=stripe_signature
        )
    except stripe.SignatureVerificationError as exc:
        logger.warning(
            "stripe signature verification failed: %s "
            "(body_bytes=%d, secret_prefix=%s)",
            exc,
            len(payload),
            settings.stripe_webhook_secret_prefix,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid signature: {exc}",
        ) from exc
    except ValueError as exc:
        # Stripe SDK raises ValueError on a malformed JSON payload.
        logger.warning("stripe webhook payload invalid json: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid payload: {exc}",
        ) from exc

    existing = await db.get(WebhookEvent, event["id"])
    if existing is not None and existing.processed_at is not None:
        return {"received": True, "duplicate": True}

    if existing is None:
        db.add(
            WebhookEvent(
                id=event["id"],
                type=event["type"],
                api_version=event.get("api_version"),
                payload=dict(event),
            )
        )
        await db.flush()

    try:
        await _dispatch(db, event)
    except Exception as exc:
        logger.exception("webhook handler failed for %s", event["id"])
        await db.rollback()
        evt = await db.get(WebhookEvent, event["id"])
        if evt is not None:
            evt.processing_error = str(exc)[:2048]
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webhook handler error",
        ) from exc

    evt = await db.get(WebhookEvent, event["id"])
    if evt is not None:
        evt.processed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"received": True}


async def _dispatch(db: AsyncSession, event: stripe.Event) -> None:
    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "payment_intent.succeeded":
        await payment_service.apply_succeeded_event(
            db,
            stripe_payment_intent_id=obj["id"],
            amount_received=int(obj.get("amount_received", obj["amount"])),
        )
    elif event_type in {"payment_intent.payment_failed", "payment_intent.canceled"}:
        # Lookup-only — local cancel already booked the reversal. Webhook just
        # syncs the terminal status if a state diverged.
        logger.info("received %s for %s", event_type, obj.get("id"))
    else:
        logger.debug("ignoring unhandled event type %s", event_type)
