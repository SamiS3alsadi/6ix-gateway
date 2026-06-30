import pytest

pytestmark = pytest.mark.asyncio


async def test_create_intent_happy_path(client, stub_stripe):
    payload = {
        "amount": 1999,
        "currency": "USD",
        "idempotency_key": "test-key-001-happy",
        "customer_id": "cus_test_1",
        "description": "test charge",
    }
    response = await client.post("/payments/intents", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["amount"] == 1999
    assert body["currency"] == "usd"
    assert body["status"] == "requires_payment_method"
    assert body["stripe_payment_intent_id"].startswith("pi_test_")
    assert stub_stripe.create_payment_intent.await_count == 1


async def test_create_intent_idempotent_replay(client, stub_stripe):
    payload = {
        "amount": 500,
        "currency": "usd",
        "idempotency_key": "test-key-002-replay",
    }
    first = await client.post("/payments/intents", json=payload)
    assert first.status_code == 201

    second = await client.post("/payments/intents", json=payload)
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    # Stripe must only be called once on a replayed idempotency_key.
    assert stub_stripe.create_payment_intent.await_count == 1


async def test_create_intent_idempotency_collision(client, stub_stripe):
    """Same key, different amount → 409 conflict."""
    await client.post(
        "/payments/intents",
        json={"amount": 100, "currency": "usd", "idempotency_key": "test-key-003-collide"},
    )
    response = await client.post(
        "/payments/intents",
        json={"amount": 999, "currency": "usd", "idempotency_key": "test-key-003-collide"},
    )
    assert response.status_code == 409


async def test_amount_must_be_positive_integer(client, stub_stripe):
    response = await client.post(
        "/payments/intents",
        json={"amount": 0, "currency": "usd", "idempotency_key": "test-key-004-zero"},
    )
    assert response.status_code == 422


async def test_retrieve_intent(client, stub_stripe):
    created = await client.post(
        "/payments/intents",
        json={"amount": 250, "currency": "usd", "idempotency_key": "test-key-005-get"},
    )
    intent_id = created.json()["id"]

    response = await client.get(f"/payments/intents/{intent_id}")
    assert response.status_code == 200
    assert response.json()["id"] == intent_id


async def test_create_intent_records_authenticated_merchant(
    client, stub_stripe, merchant_and_key
):
    """Every intent created via the public API gets stamped with merchant_id."""
    merchant_id, _ = merchant_and_key
    response = await client.post(
        "/payments/intents",
        json={
            "amount": 250,
            "currency": "usd",
            "idempotency_key": "merchant-wire-test",
        },
    )
    assert response.status_code == 201
    assert response.json()["merchant_id"] == merchant_id


async def test_cancel_intent(client, stub_stripe):
    created = await client.post(
        "/payments/intents",
        json={"amount": 1000, "currency": "usd", "idempotency_key": "test-key-006-cancel"},
    )
    intent_id = created.json()["id"]

    response = await client.post(
        f"/payments/intents/{intent_id}/cancel",
        json={"idempotency_key": "test-key-006-cancel-op", "reason": "requested_by_customer"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "canceled"
