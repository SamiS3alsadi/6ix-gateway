"""Hosted checkout sessions — merchant API + public page + webhook completion."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.checkout_session import CheckoutSession, CheckoutSessionStatus

pytestmark = pytest.mark.asyncio


# --- Helpers ---------------------------------------------------------------


async def _create_session(client, *, amount=1999, currency="usd",
                          idempotency_key="cs-test-001",
                          description="Widget",
                          success_url=None):
    body = {
        "amount": amount,
        "currency": currency,
        "idempotency_key": idempotency_key,
        "description": description,
    }
    if success_url is not None:
        body["success_url"] = success_url
    return await client.post("/checkout-sessions", json=body)


async def _create_second_merchant(no_auth_client, session_factory, email):
    from app.models.merchant import Merchant
    from app.services import api_key as api_key_service

    async with session_factory() as s:
        m = Merchant(name="Other", email=email)
        s.add(m)
        await s.commit()
        await s.refresh(m)
        _, raw = await api_key_service.create_api_key(
            s, merchant_id=m.id, name="other-test"
        )
    return m.id, {"Authorization": f"Bearer {raw}"}


# --- Auth gate -------------------------------------------------------------


async def test_create_session_requires_auth(no_auth_client, stub_stripe):
    response = await no_auth_client.post(
        "/checkout-sessions",
        json={
            "amount": 100,
            "currency": "usd",
            "idempotency_key": "no-auth-001",
        },
    )
    assert response.status_code == 401


async def test_retrieve_session_requires_auth(no_auth_client, stub_stripe):
    response = await no_auth_client.get("/checkout-sessions/cs_anything")
    assert response.status_code == 401


# --- Create + retrieve ----------------------------------------------------


async def test_create_session_happy_path(client, stub_stripe, merchant_and_key):
    merchant_id, _ = merchant_and_key
    response = await _create_session(client, amount=2500, description="A thing")
    assert response.status_code == 201
    body = response.json()
    assert body["id"].startswith("cs_")
    assert body["merchant_id"] == merchant_id
    assert body["payment_intent_id"] is not None
    assert body["amount"] == 2500
    assert body["currency"] == "usd"
    assert body["description"] == "A thing"
    assert body["status"] == "open"
    assert body["checkout_url"] == f"/checkout/{body['id']}"


async def test_create_session_idempotent_replay(client, stub_stripe):
    r1 = await _create_session(client, idempotency_key="cs-replay-001")
    r2 = await _create_session(client, idempotency_key="cs-replay-001")
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    # Stripe was only hit on the first call.
    assert stub_stripe.create_payment_intent.await_count == 1


async def test_retrieve_session_returns_current_status(client, stub_stripe):
    created = await _create_session(client, idempotency_key="cs-get-001")
    cs_id = created.json()["id"]

    response = await client.get(f"/checkout-sessions/{cs_id}")
    assert response.status_code == 200
    assert response.json()["id"] == cs_id
    assert response.json()["status"] == "open"


async def test_retrieve_unknown_session_404(client, stub_stripe):
    response = await client.get("/checkout-sessions/cs_doesnotexist")
    assert response.status_code == 404
    assert response.json()["error_code"] == "CHECKOUT_SESSION_NOT_FOUND"


# --- Cross-merchant isolation ---------------------------------------------


async def test_retrieve_session_other_merchant_404(
    client, no_auth_client, stub_stripe, session_factory
):
    # Merchant A (default `client`) creates a session.
    created = await _create_session(client, idempotency_key="cs-iso-001")
    a_cs_id = created.json()["id"]

    # Merchant B, separate auth.
    _, b_headers = await _create_second_merchant(
        no_auth_client, session_factory, email="iso-b@example.com"
    )

    r = await no_auth_client.get(
        f"/checkout-sessions/{a_cs_id}", headers=b_headers
    )
    assert r.status_code == 404
    assert r.json()["error_code"] == "CHECKOUT_SESSION_NOT_FOUND"


# --- Public checkout page -------------------------------------------------


async def test_public_page_renders_open_form(client, stub_stripe):
    created = await _create_session(
        client, amount=1999, description="Widget",
        idempotency_key="cs-pub-open-001",
    )
    cs_id = created.json()["id"]

    # Public page — no auth on the outbound request.
    page = await client.get(f"/checkout/{cs_id}", headers={"Authorization": ""})
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    html = page.text
    # Amount is formatted USD-shaped for display.
    assert "$19.99" in html
    assert "Widget" in html
    # Client secret is injected as meta content — the whole point of the page.
    assert 'name="client-secret"' in html
    # Card form + Stripe.js loader are present.
    assert 'id="card-element"' in html
    assert "js.stripe.com" in html


async def test_public_page_unknown_id_json_404(client, stub_stripe):
    # The global handler renders our structured error shape.
    response = await client.get(
        "/checkout/cs_totallyfake", headers={"Authorization": ""}
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "CHECKOUT_SESSION_NOT_FOUND"


async def test_public_page_expired_shows_message(
    client, stub_stripe, session_factory
):
    created = await _create_session(client, idempotency_key="cs-exp-001")
    cs_id = created.json()["id"]

    # Force the row into the past — bypasses the 24h TTL default.
    async with session_factory() as s:
        row = await s.get(CheckoutSession, cs_id)
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()

    page = await client.get(f"/checkout/{cs_id}", headers={"Authorization": ""})
    # 410 Gone for expired links — spec.
    assert page.status_code == 410
    assert "expired" in page.text.lower()
    # Client secret MUST NOT be rendered on the expired page — defense in depth.
    assert 'name="client-secret"' not in page.text
    assert "card-element" not in page.text

    # And the row itself should now be marked EXPIRED (lazy transition on read).
    async with session_factory() as s:
        row = await s.get(CheckoutSession, cs_id)
        assert row.status == CheckoutSessionStatus.EXPIRED


async def test_public_page_completed_shows_message(
    client, stub_stripe, session_factory
):
    created = await _create_session(client, idempotency_key="cs-comp-001")
    cs_id = created.json()["id"]

    # Force COMPLETED without going through the webhook (Section 9 tests that path).
    async with session_factory() as s:
        row = await s.get(CheckoutSession, cs_id)
        row.status = CheckoutSessionStatus.COMPLETED
        await s.commit()

    page = await client.get(f"/checkout/{cs_id}", headers={"Authorization": ""})
    assert page.status_code == 200
    assert "received" in page.text.lower() or "complete" in page.text.lower()
    # No client secret in the completed page either.
    assert 'name="client-secret"' not in page.text


# --- Webhook completion ---------------------------------------------------


async def test_webhook_success_marks_session_completed(
    client, stub_stripe, session_factory
):
    """payment_intent.succeeded for a session-linked PI must flip
    the CheckoutSession to COMPLETED."""
    created = await _create_session(client, idempotency_key="cs-hook-001")
    cs_id = created.json()["id"]
    stripe_pi_id = created.json()["payment_intent_id"]

    # Fetch the local pi's stripe_payment_intent_id — the webhook payload
    # references Stripe's id, not our internal id.
    from app.models.payment_intent import PaymentIntent

    async with session_factory() as s:
        pi = await s.get(PaymentIntent, stripe_pi_id)
        stripe_id = pi.stripe_payment_intent_id
        amount = pi.amount

    fake_event = {
        "id": "evt_cs_test_001",
        "type": "payment_intent.succeeded",
        "api_version": "2024-06-20",
        "data": {
            "object": {
                "id": stripe_id,
                "amount": amount,
                "amount_received": amount,
            }
        },
    }
    stub_stripe.construct_webhook_event.return_value = fake_event

    r = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=0,v1=fake"},
    )
    assert r.status_code == 200

    async with session_factory() as s:
        row = await s.get(CheckoutSession, cs_id)
        assert row.status == CheckoutSessionStatus.COMPLETED


async def test_webhook_completion_is_idempotent(
    client, stub_stripe, session_factory
):
    """Replaying the succeeded webhook must not error and must leave the
    session in COMPLETED."""
    created = await _create_session(client, idempotency_key="cs-hook-idem-001")
    cs_id = created.json()["id"]

    from app.models.payment_intent import PaymentIntent

    async with session_factory() as s:
        pi = await s.get(PaymentIntent, created.json()["payment_intent_id"])
        stripe_id = pi.stripe_payment_intent_id
        amount = pi.amount

    fake_event = {
        "id": "evt_cs_test_idem",
        "type": "payment_intent.succeeded",
        "api_version": "2024-06-20",
        "data": {"object": {"id": stripe_id, "amount": amount, "amount_received": amount}},
    }
    stub_stripe.construct_webhook_event.return_value = fake_event

    r1 = await client.post(
        "/webhooks/stripe", content=b"{}",
        headers={"Stripe-Signature": "t=0,v1=fake"},
    )
    r2 = await client.post(
        "/webhooks/stripe", content=b"{}",
        headers={"Stripe-Signature": "t=0,v1=fake"},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True

    async with session_factory() as s:
        row = await s.get(CheckoutSession, cs_id)
        assert row.status == CheckoutSessionStatus.COMPLETED
