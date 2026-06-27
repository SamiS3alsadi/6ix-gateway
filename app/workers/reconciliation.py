"""Periodic reconciliation worker.

Verifies that for every PaymentIntent in a terminal state, the ledger entries
sum to the expected position. Never reaches out to Stripe — webhooks are the
source of truth. This is a safety net that surfaces drift, not a fixer.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.db import session_scope
from app.models.payment_intent import PaymentIntent, PaymentIntentStatus
from app.services.ledger import list_entries_for_intent

logger = logging.getLogger(__name__)


async def reconcile_recent(window: timedelta = timedelta(hours=24)) -> dict:
    cutoff = datetime.now(timezone.utc) - window
    drift: list[dict] = []
    checked = 0

    async with session_scope() as session:
        stmt = select(PaymentIntent).where(PaymentIntent.created_at >= cutoff)
        result = await session.execute(stmt)
        for intent in result.scalars():
            checked += 1
            entries = await list_entries_for_intent(
                session, payment_intent_id=intent.id
            )
            expected_captured = (
                intent.amount_received - intent.amount_refunded
                if intent.status == PaymentIntentStatus.SUCCEEDED
                else 0
            )
            captured = sum(
                (e.amount if e.direction.value == "credit" else -e.amount)
                for e in entries
                if e.account == "payments_captured"
            )
            if captured != expected_captured:
                drift.append(
                    {
                        "intent_id": intent.id,
                        "expected": expected_captured,
                        "actual": captured,
                    }
                )

    if drift:
        logger.error("reconciliation drift detected: %s", drift)
    return {"checked": checked, "drift": drift}


async def run_forever(interval_seconds: int = 300) -> None:
    while True:
        try:
            await reconcile_recent()
        except Exception:
            logger.exception("reconciliation pass failed")
        await asyncio.sleep(interval_seconds)
