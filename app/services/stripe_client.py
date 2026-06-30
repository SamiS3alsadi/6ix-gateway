"""Thin async wrapper around stripe-python.

Stripe's official SDK is sync; we offload calls to a thread so the FastAPI
event loop stays unblocked. Every mutating call accepts an `idempotency_key`
that's forwarded as the `Idempotency-Key` request header.
"""
from __future__ import annotations

import asyncio
from typing import Any

import stripe

from app.core.config import settings

stripe.api_key = settings.stripe_api_key


class StripeClient:
    """Tiny facade so service code never touches the stripe module directly."""

    async def create_payment_intent(
        self,
        *,
        amount: int,
        currency: str,
        idempotency_key: str,
        customer: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> stripe.PaymentIntent:
        return await asyncio.to_thread(
            stripe.PaymentIntent.create,
            amount=amount,
            currency=currency,
            customer=customer,
            description=description,
            metadata=metadata or {},
            idempotency_key=idempotency_key,
        )

    async def confirm_payment_intent(
        self,
        *,
        stripe_payment_intent_id: str,
        payment_method: str,
        idempotency_key: str,
    ) -> stripe.PaymentIntent:
        return await asyncio.to_thread(
            stripe.PaymentIntent.confirm,
            stripe_payment_intent_id,
            payment_method=payment_method,
            idempotency_key=idempotency_key,
        )

    async def cancel_payment_intent(
        self,
        *,
        stripe_payment_intent_id: str,
        idempotency_key: str,
        cancellation_reason: str | None = None,
    ) -> stripe.PaymentIntent:
        kwargs: dict[str, Any] = {"idempotency_key": idempotency_key}
        if cancellation_reason:
            kwargs["cancellation_reason"] = cancellation_reason
        return await asyncio.to_thread(
            stripe.PaymentIntent.cancel, stripe_payment_intent_id, **kwargs
        )

    async def retrieve_payment_intent(
        self, stripe_payment_intent_id: str
    ) -> stripe.PaymentIntent:
        return await asyncio.to_thread(
            stripe.PaymentIntent.retrieve, stripe_payment_intent_id
        )

    async def create_refund(
        self,
        *,
        stripe_payment_intent_id: str,
        amount: int | None,
        idempotency_key: str,
        reason: str | None = None,
    ) -> stripe.Refund:
        kwargs: dict[str, Any] = {
            "payment_intent": stripe_payment_intent_id,
            "idempotency_key": idempotency_key,
        }
        if amount is not None:
            kwargs["amount"] = amount
        if reason:
            kwargs["reason"] = reason
        return await asyncio.to_thread(stripe.Refund.create, **kwargs)

    def construct_webhook_event(
        self, *, payload: bytes, sig_header: str
    ) -> stripe.Event:
        """Verify signature and parse a webhook event. Sync — no I/O."""
        return stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=settings.stripe_webhook_secret,
        )

    async def list_balance_transactions(
        self,
        *,
        created_gte: int,
        created_lt: int,
        types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Page through balance transactions for the [gte, lt) UTC window.

        Returns the BTs with their `source` expanded so the caller can map a
        charge → payment_intent without a second round trip. Stripe paginates
        at 100; we walk the cursor until exhausted.
        """

        def _list() -> list[dict[str, Any]]:
            collected: list[dict[str, Any]] = []
            params: dict[str, Any] = {
                "created": {"gte": created_gte, "lt": created_lt},
                "limit": 100,
                "expand": ["data.source"],
            }
            if types:
                # The API accepts one `type` at a time; iterate.
                for t in types:
                    page_params = {**params, "type": t}
                    while True:
                        page = stripe.BalanceTransaction.list(**page_params)
                        collected.extend(d.to_dict() for d in page.data)
                        if not page.has_more:
                            break
                        page_params = {
                            **page_params,
                            "starting_after": page.data[-1].id,
                        }
                return collected

            while True:
                page = stripe.BalanceTransaction.list(**params)
                collected.extend(d.to_dict() for d in page.data)
                if not page.has_more:
                    break
                params = {**params, "starting_after": page.data[-1].id}
            return collected

        return await asyncio.to_thread(_list)


stripe_client = StripeClient()
