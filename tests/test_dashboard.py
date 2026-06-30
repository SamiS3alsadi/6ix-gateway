import pytest

from app.models.payment_intent import PaymentIntent, PaymentIntentStatus

pytestmark = pytest.mark.asyncio

AUTH = {"X-API-Key": "test-dashboard-key"}


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


async def test_dashboard_requires_api_key(client, stub_stripe):
    # No header → 401.
    response = await client.get("/dashboard/balance")
    assert response.status_code == 401


async def test_balance_empty_ledger(client, stub_stripe):
    response = await client.get("/dashboard/balance", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"currencies": []}


async def test_balance_groups_per_currency_per_account(client, stub_stripe):
    # One intent in USD, one in EUR — every create writes 2 ledger legs.
    await _seed_intent(client, None, amount=1000, currency="usd")
    await _seed_intent(client, None, amount=500, currency="eur", customer="cus_eu")

    response = await client.get("/dashboard/balance", headers=AUTH)
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
        "/dashboard/transactions?page=1&page_size=3", headers=AUTH
    )
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["total"] == 7
    assert b1["page"] == 1
    assert b1["page_size"] == 3
    assert len(b1["items"]) == 3

    r2 = await client.get(
        "/dashboard/transactions?page=3&page_size=3", headers=AUTH
    )
    b2 = r2.json()
    assert len(b2["items"]) == 1  # 7 total, page 3 size 3 → 1 remaining


async def test_transactions_filter_by_status_and_currency(client, stub_stripe, session_factory):
    await _seed_intent(client, session_factory, amount=100, customer="cus_f1", succeed=True)
    await _seed_intent(client, session_factory, amount=200, customer="cus_f2")
    await _seed_intent(client, session_factory, amount=300, currency="eur", customer="cus_f3")

    # Filter by status
    r = await client.get(
        "/dashboard/transactions?status=succeeded", headers=AUTH
    )
    assert r.status_code == 200
    assert all(item["status"] == "succeeded" for item in r.json()["items"])

    # Filter by currency
    r = await client.get(
        "/dashboard/transactions?currency=eur", headers=AUTH
    )
    assert all(item["currency"] == "eur" for item in r.json()["items"])


async def test_transaction_detail_includes_ledger(client, stub_stripe, session_factory):
    pid = await _seed_intent(client, session_factory, amount=1500, customer="cus_detail")

    response = await client.get(
        f"/dashboard/transactions/{pid}", headers=AUTH
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == pid
    # Create writes 2 ledger entries (debit pending / credit authorised).
    assert len(body["ledger_entries"]) == 2
    accounts = {e["account"] for e in body["ledger_entries"]}
    assert "payments_authorised" in accounts


async def test_transaction_detail_404(client, stub_stripe):
    response = await client.get(
        "/dashboard/transactions/pi_local_nope", headers=AUTH
    )
    assert response.status_code == 404
