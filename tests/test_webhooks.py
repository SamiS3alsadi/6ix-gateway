import pytest

from app.models.payment_intent import PaymentIntentStatus

pytestmark = pytest.mark.asyncio


async def test_webhook_succeeded_advances_status(client, stub_stripe):
    # Create an intent so the webhook has a local row to update.
    created = await client.post(
        "/payments/intents",
        json={
            "amount": 2500,
            "currency": "usd",
            "idempotency_key": "wh-test-001",
        },
    )
    intent_id = created.json()["id"]
    stripe_pi_id = created.json()["stripe_payment_intent_id"]

    fake_event = {
        "id": "evt_test_001",
        "type": "payment_intent.succeeded",
        "api_version": "2024-06-20",
        "data": {
            "object": {
                "id": stripe_pi_id,
                "amount": 2500,
                "amount_received": 2500,
            }
        },
    }
    stub_stripe.construct_webhook_event.return_value = fake_event

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=0,v1=fake"},
    )
    assert response.status_code == 200
    assert response.json()["received"] is True

    refetched = await client.get(f"/payments/intents/{intent_id}")
    assert refetched.json()["status"] == PaymentIntentStatus.SUCCEEDED.value
    assert refetched.json()["amount_received"] == 2500


async def test_webhook_replay_is_idempotent(client, stub_stripe):
    created = await client.post(
        "/payments/intents",
        json={"amount": 100, "currency": "usd", "idempotency_key": "wh-test-002"},
    )
    stripe_pi_id = created.json()["stripe_payment_intent_id"]

    fake_event = {
        "id": "evt_test_002",
        "type": "payment_intent.succeeded",
        "api_version": "2024-06-20",
        "data": {"object": {"id": stripe_pi_id, "amount": 100, "amount_received": 100}},
    }
    stub_stripe.construct_webhook_event.return_value = fake_event

    first = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=0,v1=fake"},
    )
    assert first.status_code == 200

    second = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=0,v1=fake"},
    )
    assert second.status_code == 200
    assert second.json().get("duplicate") is True


async def test_webhook_missing_signature_rejected(client, stub_stripe):
    response = await client.post("/webhooks/stripe", content=b"{}")
    assert response.status_code == 400
