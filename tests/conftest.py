"""Shared pytest fixtures.

Tests run against an in-memory SQLite database with the JSONB columns mapped
through SQLAlchemy's variant fallback. Stripe is stubbed so no network calls
ever happen.
"""
from __future__ import annotations

import os
import uuid
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("STRIPE_API_KEY", "sk_test_fake")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_fake")
os.environ.setdefault("DASHBOARD_API_KEY", "test-dashboard-key")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.types import JSON

# Make JSONB usable on SQLite for tests.
JSONB.impl = JSON  # type: ignore[assignment]

from app.core import db as db_module
from app.core.db import Base, get_db
from app.services import stripe_client as stripe_client_module
from main import app


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncIterator[AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def stub_stripe(monkeypatch):
    """Replace the Stripe facade with a controllable mock."""
    fake = MagicMock()

    def _fake_intent(amount: int, currency: str, status: str = "requires_payment_method"):
        return {
            "id": f"pi_test_{uuid.uuid4().hex[:16]}",
            "amount": amount,
            "currency": currency,
            "status": status,
            "client_secret": f"pi_test_secret_{uuid.uuid4().hex[:12]}",
        }

    async def create_payment_intent(*, amount, currency, idempotency_key, **kwargs):
        return _fake_intent(amount, currency)

    async def confirm_payment_intent(*, stripe_payment_intent_id, payment_method, idempotency_key):
        return {
            "id": stripe_payment_intent_id,
            "status": "succeeded",
        }

    async def cancel_payment_intent(*, stripe_payment_intent_id, idempotency_key, cancellation_reason=None):
        return {"id": stripe_payment_intent_id, "status": "canceled"}

    async def create_refund(*, stripe_payment_intent_id, amount, idempotency_key, reason=None):
        return {
            "id": f"re_test_{uuid.uuid4().hex[:16]}",
            "status": "succeeded",
            "amount": amount,
        }

    fake.create_payment_intent = AsyncMock(side_effect=create_payment_intent)
    fake.confirm_payment_intent = AsyncMock(side_effect=confirm_payment_intent)
    fake.cancel_payment_intent = AsyncMock(side_effect=cancel_payment_intent)
    fake.create_refund = AsyncMock(side_effect=create_refund)
    fake.retrieve_payment_intent = AsyncMock()
    fake.construct_webhook_event = MagicMock()

    monkeypatch.setattr(stripe_client_module, "stripe_client", fake)
    # Also patch the symbol re-exported into service modules.
    from app.services import payment as payment_service
    from app.api import refunds as refunds_router
    from app.api import webhooks as webhooks_router

    monkeypatch.setattr(payment_service, "stripe_client", fake)
    monkeypatch.setattr(refunds_router, "stripe_client", fake)
    monkeypatch.setattr(webhooks_router, "stripe_client", fake)
    return fake
