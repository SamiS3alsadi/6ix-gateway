import pytest

from app.models.payment_intent import PaymentIntent, PaymentIntentStatus
from app.models.ledger import LedgerEntry, LedgerEntryDirection

pytestmark = pytest.mark.asyncio


async def _make_succeeded_intent(client, session_factory, *, amount: int = 1000, customer="cus_r"):
    """Create an intent + force it into SUCCEEDED state so it can be refunded."""
    created = await client.post(
        "/payments/intents",
        json={
            "amount": amount,
            "currency": "usd",
            "idempotency_key": f"refund-setup-{amount}-{customer}",
            "customer_id": customer,
        },
    )
    body = created.json()
    # Hand-promote the intent via the DB — webhooks would normally do this.
    async with session_factory() as s:
        intent = await s.get(PaymentIntent, body["id"])
        intent.status = PaymentIntentStatus.SUCCEEDED
        intent.amount_received = amount
        await s.commit()
    return body["id"]


async def test_full_refund_marks_intent_fully_refunded(client, stub_stripe, session_factory):
    intent_id = await _make_succeeded_intent(client, session_factory, amount=1000)

    response = await client.post(
        f"/payments/intents/{intent_id}/refund",
        json={"idempotency_key": "refund-full-1"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["amount_refunded"] == 1000
    assert body["status"] == "succeeded"
    stub_stripe.create_refund.assert_awaited_once()


async def test_partial_refund_accumulates(client, stub_stripe, session_factory):
    intent_id = await _make_succeeded_intent(client, session_factory, amount=1000, customer="cus_partial")

    first = await client.post(
        f"/payments/intents/{intent_id}/refund",
        json={"amount": 300, "idempotency_key": "refund-partial-1"},
    )
    assert first.status_code == 201
    assert first.json()["amount_refunded"] == 300

    second = await client.post(
        f"/payments/intents/{intent_id}/refund",
        json={"amount": 200, "idempotency_key": "refund-partial-2"},
    )
    assert second.status_code == 201
    assert second.json()["amount_refunded"] == 500


async def test_refund_exceeding_refundable_rejected(client, stub_stripe, session_factory):
    intent_id = await _make_succeeded_intent(client, session_factory, amount=500, customer="cus_over")

    response = await client.post(
        f"/payments/intents/{intent_id}/refund",
        json={"amount": 1000, "idempotency_key": "refund-over-1"},
    )
    assert response.status_code == 422


async def test_refund_on_non_succeeded_intent_rejected(client, stub_stripe):
    # Create but don't promote to SUCCEEDED.
    created = await client.post(
        "/payments/intents",
        json={"amount": 100, "currency": "usd", "idempotency_key": "refund-nostate"},
    )
    intent_id = created.json()["id"]
    response = await client.post(
        f"/payments/intents/{intent_id}/refund",
        json={"idempotency_key": "refund-nostate-op"},
    )
    assert response.status_code == 409


async def test_refund_unknown_intent_404(client, stub_stripe):
    response = await client.post(
        "/payments/intents/pi_local_doesnotexist/refund",
        json={"idempotency_key": "refund-missing-1"},
    )
    assert response.status_code == 404


async def test_refund_writes_double_entry_ledger(client, stub_stripe, session_factory):
    intent_id = await _make_succeeded_intent(client, session_factory, amount=2000, customer="cus_ledger")

    response = await client.post(
        f"/payments/intents/{intent_id}/refund",
        json={"amount": 800, "idempotency_key": "refund-ledger-1"},
    )
    assert response.status_code == 201

    async with session_factory() as s:
        from sqlalchemy import select
        rows = (
            await s.execute(
                select(LedgerEntry)
                .where(LedgerEntry.payment_intent_id == intent_id)
                .where(LedgerEntry.description.like("refund:%"))
            )
        ).scalars().all()

    assert len(rows) == 2  # one debit + one credit
    debit = next(r for r in rows if r.direction == LedgerEntryDirection.DEBIT)
    credit = next(r for r in rows if r.direction == LedgerEntryDirection.CREDIT)
    assert debit.account == "payments_captured"
    assert credit.account == "customer:cus_ledger"
    assert debit.amount == credit.amount == 800
    assert debit.transaction_id == credit.transaction_id
