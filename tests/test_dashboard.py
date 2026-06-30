import pytest

from app.models.payment_intent import PaymentIntent, PaymentIntentStatus

pytestmark = pytest.mark.asyncio


async def _seed_intent(client, session_factory, *, amount, currency="usd", customer="cus_d", succeed=False):
    created = await client.post(
        "/payments/intents",
        json={
            "amount": amount,
            "currency": currency,
            "idempotency_key": f"dash-{amount}-{currency}-{customer}",
            "customer_id": customer,
        },
    )
    pid = created.json()["id"]
    if succeed:
        async with session_factory() as s:
            intent = await s.get(PaymentIntent, pid)
            intent.status = PaymentIntentStatus.SUCCEEDED
            intent.amount_received = amount
            await s.commit()
    return pid


async def test_dashboard_requires_api_key(no_auth_client, stub_stripe):
    # No Authorization header → 401.
    response = await no_auth_client.get("/dashboard/balance")
    assert response.status_code == 401


async def test_balance_empty_ledger(client, stub_stripe):
    response = await client.get("/dashboard/balance")
    assert response.status_code == 200
    assert response.json() == {"currencies": []}


async def test_balance_groups_per_currency_per_account(client, stub_stripe):
    # One intent in USD, one in EUR — every create writes 2 ledger legs.
    await _seed_intent(client, None, amount=1000, currency="usd")
    await _seed_intent(client, None, amount=500, currency="eur", customer="cus_eu")

    response = await client.get("/dashboard/balance")
    assert response.status_code == 200
    body = response.json()
    currencies = {c["currency"]: c for c in body["currencies"]}
    assert set(currencies.keys()) == {"usd", "eur"}
    # Each currency's net should be 0 — balanced ledger invariant.
    for c in currencies.values():
        assert c["net"] == 0
        # Should have payments_authorised + customer_pending:* accounts.
        accounts = {a["account"]: a["balance"] for a in c["accounts"]}
        assert "payments_authorised" in accounts


async def test_transactions_pagination_and_total(client, stub_stripe, session_factory):
    for i in range(7):
        await _seed_intent(client, session_factory, amount=100 + i, customer=f"cus_p{i}")

    r1 = await client.get(
        "/dashboard/transactions?page=1&page_size=3"    )
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["total"] == 7
    assert b1["page"] == 1
    assert b1["page_size"] == 3
    assert len(b1["items"]) == 3

    r2 = await client.get(
        "/dashboard/transactions?page=3&page_size=3"    )
    b2 = r2.json()
    assert len(b2["items"]) == 1  # 7 total, page 3 size 3 → 1 remaining


async def test_transactions_filter_by_status_and_currency(client, stub_stripe, session_factory):
    await _seed_intent(client, session_factory, amount=100, customer="cus_f1", succeed=True)
    await _seed_intent(client, session_factory, amount=200, customer="cus_f2")
    await _seed_intent(client, session_factory, amount=300, currency="eur", customer="cus_f3")

    # Filter by status
    r = await client.get(
        "/dashboard/transactions?status=succeeded"    )
    assert r.status_code == 200
    assert all(item["status"] == "succeeded" for item in r.json()["items"])

    # Filter by currency
    r = await client.get(
        "/dashboard/transactions?currency=eur"    )
    assert all(item["currency"] == "eur" for item in r.json()["items"])


async def test_transaction_detail_includes_ledger(client, stub_stripe, session_factory):
    pid = await _seed_intent(client, session_factory, amount=1500, customer="cus_detail")

    response = await client.get(
        f"/dashboard/transactions/{pid}"    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == pid
    # Create writes 2 ledger entries (debit pending / credit authorised).
    assert len(body["ledger_entries"]) == 2
    accounts = {e["account"] for e in body["ledger_entries"]}
    assert "payments_authorised" in accounts


async def test_transaction_detail_404(client, stub_stripe):
    response = await client.get(
        "/dashboard/transactions/pi_local_nope"    )
    assert response.status_code == 404


# --- Cross-merchant isolation ----------------------------------------------


async def _create_second_merchant_client(no_auth_client, session_factory, email):
    """Seed a second merchant + key and return (merchant_id, auth_header_dict)."""
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


async def test_balance_scoped_to_authenticated_merchant(
    client, no_auth_client, stub_stripe, session_factory
):
    """Merchant A's payment activity must not show up in Merchant B's balance."""
    # client is auth'd as the default test merchant — seed it with activity.
    await _seed_intent(client, session_factory, amount=1000, customer="cus_a")

    # Spin up a separate merchant B with its own key.
    _, b_headers = await _create_second_merchant_client(
        no_auth_client, session_factory, email="b@example.com"
    )

    # Merchant B's balance is empty — they have no payment activity yet.
    response = await no_auth_client.get("/dashboard/balance", headers=b_headers)
    assert response.status_code == 200
    assert response.json() == {"currencies": []}


async def test_transactions_list_scoped_to_authenticated_merchant(
    client, no_auth_client, stub_stripe, session_factory
):
    # Default test merchant creates 3 intents.
    for i in range(3):
        await _seed_intent(client, session_factory, amount=100 + i, customer=f"cus_x{i}")

    _, b_headers = await _create_second_merchant_client(
        no_auth_client, session_factory, email="b-list@example.com"
    )

    # Merchant B sees zero of A's intents.
    r = await no_auth_client.get("/dashboard/transactions", headers=b_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_transaction_detail_404_for_other_merchants_intent(
    client, no_auth_client, stub_stripe, session_factory
):
    """Merchant B fetching Merchant A's intent by id must get 404, not the data."""
    # A's intent.
    a_intent_id = await _seed_intent(
        client, session_factory, amount=999, customer="cus_a_secret"
    )

    _, b_headers = await _create_second_merchant_client(
        no_auth_client, session_factory, email="b-detail@example.com"
    )

    r = await no_auth_client.get(
        f"/dashboard/transactions/{a_intent_id}", headers=b_headers
    )
    assert r.status_code == 404
    assert r.json()["error_code"] == "PAYMENT_NOT_FOUND"
