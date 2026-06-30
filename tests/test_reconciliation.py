"""Reconciliation worker tests — fully stubbed Stripe, in-memory DB."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.models.payment_intent import PaymentIntent, PaymentIntentStatus
from app.workers import reconciliation as recon

pytestmark = pytest.mark.asyncio


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def bound_session_scope(session_factory, monkeypatch):
    """Replace the worker's session_scope with one backed by the test factory.

    The worker imports session_scope at module load, so patching app.core.db
    has no effect — we have to patch the imported binding directly.
    """

    @asynccontextmanager
    async def _scope():
        async with session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(recon, "session_scope", _scope)
    return _scope


@pytest.fixture
def stub_stripe_recon(monkeypatch):
    """Inject a Stripe stub the worker can reach via its imported binding."""
    fake_list = AsyncMock(return_value=[])
    monkeypatch.setattr(
        recon.stripe_client, "list_balance_transactions", fake_list
    )
    return fake_list


# --- Helpers ----------------------------------------------------------------


def _bt(pi_id: str, amount: int, *, kind: str = "charge") -> dict:
    """Build a fake expanded BalanceTransaction dict."""
    return {
        "id": f"txn_{pi_id}_{kind}_{amount}",
        "type": kind,
        "amount": amount,
        "currency": "usd",
        "source": {
            "id": f"ch_{pi_id}" if kind == "charge" else f"re_{pi_id}",
            "payment_intent": pi_id,
        },
    }


async def _insert_intent(
    session_factory,
    *,
    stripe_id: str,
    amount: int,
    amount_received: int,
    amount_refunded: int = 0,
    status: PaymentIntentStatus = PaymentIntentStatus.SUCCEEDED,
    updated_at: datetime,
):
    async with session_factory() as s:
        intent = PaymentIntent(
            stripe_payment_intent_id=stripe_id,
            idempotency_key=f"idk-{stripe_id}",
            amount=amount,
            currency="usd",
            amount_received=amount_received,
            amount_refunded=amount_refunded,
            status=status,
        )
        s.add(intent)
        await s.commit()
        # Force updated_at into the target window — SQLAlchemy defaults to now().
        intent.updated_at = updated_at
        await s.commit()


# --- Tests ------------------------------------------------------------------


async def test_bt_payment_intent_id_extracts_from_dict_source():
    bt = _bt("pi_test_1", 1000)
    assert recon._bt_payment_intent_id(bt) == "pi_test_1"


async def test_bt_payment_intent_id_none_when_source_missing():
    assert recon._bt_payment_intent_id({"type": "payout", "source": None}) is None
    assert recon._bt_payment_intent_id({"type": "payout"}) is None


async def test_reconcile_empty_day(bound_session_scope, stub_stripe_recon):
    run = await recon.reconcile_day(date(2026, 6, 28))
    assert run.run_date == date(2026, 6, 28)
    assert run.total_stripe == 0
    assert run.total_internal == 0
    assert run.mismatches_count == 0
    assert run.mismatches == []


async def test_reconcile_perfect_match(
    bound_session_scope, stub_stripe_recon, session_factory
):
    target = date(2026, 6, 28)
    in_window = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)

    stub_stripe_recon.return_value = [_bt("pi_match", 1500)]
    await _insert_intent(
        session_factory,
        stripe_id="pi_match",
        amount=1500,
        amount_received=1500,
        updated_at=in_window,
    )

    run = await recon.reconcile_day(target)
    assert run.total_stripe == 1500
    assert run.total_internal == 1500
    assert run.mismatches_count == 0


async def test_reconcile_missing_internal(
    bound_session_scope, stub_stripe_recon
):
    # Stripe has activity for a PI we never recorded.
    stub_stripe_recon.return_value = [_bt("pi_only_stripe", 700)]
    run = await recon.reconcile_day(date(2026, 6, 28))
    assert run.mismatches_count == 1
    m = run.mismatches[0]
    assert m["type"] == "missing_internal"
    assert m["stripe_payment_intent_id"] == "pi_only_stripe"
    assert m["stripe_amount"] == 700


async def test_reconcile_missing_stripe(
    bound_session_scope, stub_stripe_recon, session_factory
):
    in_window = datetime(2026, 6, 28, 9, 0, tzinfo=timezone.utc)
    # Our DB says succeeded but Stripe returned nothing.
    await _insert_intent(
        session_factory,
        stripe_id="pi_only_internal",
        amount=400,
        amount_received=400,
        updated_at=in_window,
    )
    run = await recon.reconcile_day(date(2026, 6, 28))
    assert run.mismatches_count == 1
    m = run.mismatches[0]
    assert m["type"] == "missing_stripe"
    assert m["internal_amount"] == 400


async def test_reconcile_amount_mismatch(
    bound_session_scope, stub_stripe_recon, session_factory
):
    in_window = datetime(2026, 6, 28, 9, 0, tzinfo=timezone.utc)
    stub_stripe_recon.return_value = [_bt("pi_drift", 1000)]
    await _insert_intent(
        session_factory,
        stripe_id="pi_drift",
        amount=900,
        amount_received=900,  # Stripe says 1000, we say 900
        updated_at=in_window,
    )

    run = await recon.reconcile_day(date(2026, 6, 28))
    assert run.mismatches_count == 1
    m = run.mismatches[0]
    assert m["type"] == "amount_mismatch"
    assert m["stripe_amount"] == 1000
    assert m["internal_amount"] == 900
    assert m["diff"] == 100


async def test_reconcile_refunds_net_against_charges(
    bound_session_scope, stub_stripe_recon, session_factory
):
    """A $10 charge + $3 refund nets to $7 on both sides."""
    in_window = datetime(2026, 6, 28, 14, 0, tzinfo=timezone.utc)
    stub_stripe_recon.return_value = [
        _bt("pi_with_refund", 1000, kind="charge"),
        _bt("pi_with_refund", -300, kind="refund"),
    ]
    await _insert_intent(
        session_factory,
        stripe_id="pi_with_refund",
        amount=1000,
        amount_received=1000,
        amount_refunded=300,
        updated_at=in_window,
    )
    run = await recon.reconcile_day(date(2026, 6, 28))
    assert run.total_stripe == 700
    assert run.total_internal == 700
    assert run.mismatches_count == 0
