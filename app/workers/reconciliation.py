"""Reconciliation worker.

Two passes exist here:

1. `reconcile_day(date)` — compares our books to Stripe's. Pulls yesterday's
   balance transactions, matches each against `PaymentIntent` records via
   `stripe_payment_intent_id`, classifies mismatches, and persists a
   `ReconciliationRun` row.

2. `reconcile_recent()` — self-check on our own ledger to surface drift in
   double-entry pairs. Doesn't reach out to Stripe. Webhooks remain the source
   of truth for status transitions; this is a safety net.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.db import session_scope
from app.models.payment_intent import PaymentIntent, PaymentIntentStatus
from app.models.reconciliation_run import ReconciliationRun
from app.services.ledger import list_entries_for_intent
from app.services.stripe_client import stripe_client

logger = logging.getLogger(__name__)


def _bt_payment_intent_id(bt: dict[str, Any]) -> str | None:
    """Pull the pi_... id from a balance transaction's expanded source."""
    source = bt.get("source")
    if isinstance(source, dict):
        # Charges and refunds both expose `payment_intent` on the expanded source.
        pi = source.get("payment_intent")
        if isinstance(pi, str):
            return pi
        if isinstance(pi, dict):
            return pi.get("id")
    return None


async def reconcile_day(target_date: date) -> ReconciliationRun:
    """Reconcile our PaymentIntents against Stripe for a single UTC day.

    Mismatch types written to ReconciliationRun.mismatches:
      - missing_internal: Stripe has activity for a pi we have no record of
      - missing_stripe:   we marked a pi succeeded but Stripe has no BT
      - amount_mismatch:  both sides have it, but the net amounts disagree
    """
    start = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    # --- Stripe side ----------------------------------------------------
    bts = await stripe_client.list_balance_transactions(
        created_gte=int(start.timestamp()),
        created_lt=int(end.timestamp()),
        types=["charge", "refund"],
    )

    stripe_by_pi: dict[str, int] = {}
    for bt in bts:
        pi_id = _bt_payment_intent_id(bt)
        if pi_id is None:
            continue
        # Charges have positive amount, refunds negative — summing nets them.
        stripe_by_pi[pi_id] = stripe_by_pi.get(pi_id, 0) + int(bt["amount"])

    total_stripe = sum(stripe_by_pi.values())

    # --- Internal side --------------------------------------------------
    async with session_scope() as session:
        result = await session.execute(
            select(PaymentIntent).where(
                PaymentIntent.stripe_payment_intent_id.is_not(None),
                PaymentIntent.updated_at >= start,
                PaymentIntent.updated_at < end,
            )
        )
        internal_intents = {
            i.stripe_payment_intent_id: i
            for i in result.scalars().all()
        }
        internal_by_pi: dict[str, int] = {
            pi_id: i.amount_received - i.amount_refunded
            for pi_id, i in internal_intents.items()
            if i.status == PaymentIntentStatus.SUCCEEDED
        }
        total_internal = sum(internal_by_pi.values())

        # --- Classify -----------------------------------------------------
        mismatches: list[dict[str, Any]] = []
        for pi_id in sorted(set(stripe_by_pi) | set(internal_by_pi)):
            s_amt = stripe_by_pi.get(pi_id)
            i_amt = internal_by_pi.get(pi_id)
            if s_amt is None:
                mismatches.append(
                    {
                        "type": "missing_stripe",
                        "stripe_payment_intent_id": pi_id,
                        "internal_amount": i_amt,
                    }
                )
            elif i_amt is None:
                mismatches.append(
                    {
                        "type": "missing_internal",
                        "stripe_payment_intent_id": pi_id,
                        "stripe_amount": s_amt,
                    }
                )
            elif s_amt != i_amt:
                mismatches.append(
                    {
                        "type": "amount_mismatch",
                        "stripe_payment_intent_id": pi_id,
                        "stripe_amount": s_amt,
                        "internal_amount": i_amt,
                        "diff": s_amt - i_amt,
                    }
                )

        run = ReconciliationRun(
            run_date=target_date,
            total_stripe=total_stripe,
            total_internal=total_internal,
            mismatches_count=len(mismatches),
            mismatches=mismatches,
        )
        session.add(run)
        await session.flush()

        for m in mismatches:
            logger.warning("reconciliation mismatch: %s", m)
        logger.info(
            "reconciliation run %s for %s: stripe=%d internal=%d mismatches=%d",
            run.id,
            target_date.isoformat(),
            total_stripe,
            total_internal,
            len(mismatches),
        )
        # session_scope commits on exit. expire_on_commit=False means the run
        # object stays usable to the caller.
        return run


async def reconcile_yesterday() -> ReconciliationRun:
    """Convenience wrapper — reconciles the previous UTC day."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    return await reconcile_day(yesterday)


# --- Internal-only self-check ------------------------------------------------


async def reconcile_recent(window: timedelta = timedelta(hours=24)) -> dict:
    """Self-check: walk recent intents and verify the ledger position matches
    `amount_received - amount_refunded`. Does not touch Stripe."""
    cutoff = datetime.now(timezone.utc) - window
    drift: list[dict] = []
    checked = 0

    async with session_scope() as session:
        result = await session.execute(
            select(PaymentIntent).where(PaymentIntent.created_at >= cutoff)
        )
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
