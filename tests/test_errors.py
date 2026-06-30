"""Lock down the structured error contract.

Every error response must be {"error_code", "message", "detail"} and the
status code + error_code pair must match what we documented.
"""
import pytest

from app.core.errors import ErrorCode

pytestmark = pytest.mark.asyncio


def _assert_error_shape(body: dict) -> None:
    assert set(body.keys()) == {"error_code", "message", "detail"}
    assert isinstance(body["error_code"], str)
    assert isinstance(body["message"], str)
    assert body["detail"] is None or isinstance(body["detail"], str)


async def test_unauthorized_shape(no_auth_client):
    r = await no_auth_client.get("/dashboard/balance")  # no Authorization header
    assert r.status_code == 401
    body = r.json()
    _assert_error_shape(body)
    assert body["error_code"] == ErrorCode.UNAUTHORIZED.value


async def test_payment_not_found_shape(client, stub_stripe):
    r = await client.get("/payments/intents/pi_local_nope")
    assert r.status_code == 404
    body = r.json()
    _assert_error_shape(body)
    assert body["error_code"] == ErrorCode.PAYMENT_NOT_FOUND.value
    assert "pi_local_nope" in (body["detail"] or "")


async def test_idempotency_conflict_shape(client, stub_stripe):
    payload = {
        "amount": 100,
        "currency": "usd",
        "idempotency_key": "err-shape-conflict",
    }
    await client.post("/payments/intents", json=payload)
    r = await client.post(
        "/payments/intents",
        json={**payload, "amount": 999},
    )
    assert r.status_code == 409
    body = r.json()
    _assert_error_shape(body)
    assert body["error_code"] == ErrorCode.IDEMPOTENCY_CONFLICT.value


async def test_validation_error_shape(client, stub_stripe):
    # amount=0 fails Pydantic ge=1 → RequestValidationError → 422
    r = await client.post(
        "/payments/intents",
        json={"amount": 0, "currency": "usd", "idempotency_key": "err-validation"},
    )
    assert r.status_code == 422
    body = r.json()
    _assert_error_shape(body)
    assert body["error_code"] == ErrorCode.VALIDATION_ERROR.value


async def test_invalid_signature_shape(client, stub_stripe):
    r = await client.post(
        "/webhooks/stripe", content=b"{}"
    )  # no Stripe-Signature header
    assert r.status_code == 400
    body = r.json()
    _assert_error_shape(body)
    assert body["error_code"] == ErrorCode.INVALID_SIGNATURE.value


async def test_refund_invalid_amount_shape(client, stub_stripe, session_factory):
    from app.models.payment_intent import PaymentIntent, PaymentIntentStatus

    created = await client.post(
        "/payments/intents",
        json={
            "amount": 500,
            "currency": "usd",
            "idempotency_key": "err-refund-amt",
            "customer_id": "cus_err",
        },
    )
    pid = created.json()["id"]
    async with session_factory() as s:
        intent = await s.get(PaymentIntent, pid)
        intent.status = PaymentIntentStatus.SUCCEEDED
        intent.amount_received = 500
        await s.commit()

    r = await client.post(
        f"/payments/intents/{pid}/refund",
        json={"amount": 99999, "idempotency_key": "err-refund-over"},
    )
    assert r.status_code == 422
    body = r.json()
    _assert_error_shape(body)
    assert body["error_code"] == ErrorCode.INVALID_AMOUNT.value
