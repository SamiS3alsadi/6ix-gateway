"""Bearer-auth coverage — the merchant API gate.

Covers the contract in Section 9:
  * missing / invalid / revoked key → 401
  * valid key → request proceeds
  * prefix-lookup collision handled correctly
  * revoked key rejected even if hash matches
"""
import hashlib

import pytest

from app.core.errors import ErrorCode, UnauthorizedError
from app.models.api_key import APIKey
from app.models.merchant import Merchant
from app.services import api_key as api_key_service

pytestmark = pytest.mark.asyncio


# --- Header-level rejection (no DB needed) ----------------------------------


async def test_missing_authorization_header_is_401(no_auth_client):
    response = await no_auth_client.get("/payments/intents/whatever")
    assert response.status_code == 401
    assert response.json()["error_code"] == ErrorCode.UNAUTHORIZED.value


async def test_wrong_scheme_is_401(no_auth_client):
    # FastAPI's HTTPBearer parses only the Bearer scheme — anything else
    # is treated as "no credentials present". Either way we want 401.
    response = await no_auth_client.get(
        "/payments/intents/whatever",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == ErrorCode.UNAUTHORIZED.value


async def test_malformed_key_is_401(no_auth_client):
    response = await no_auth_client.get(
        "/payments/intents/whatever",
        headers={"Authorization": "Bearer not-a-real-key"},
    )
    assert response.status_code == 401


async def test_well_formed_but_unknown_key_is_401(no_auth_client):
    # Right shape, zero rows in DB match this prefix.
    fake_key = "gw_live_" + "0" * 64
    response = await no_auth_client.get(
        "/payments/intents/whatever",
        headers={"Authorization": f"Bearer {fake_key}"},
    )
    assert response.status_code == 401


# --- Valid-key happy path ---------------------------------------------------


async def test_valid_key_lets_request_through(client, stub_stripe):
    """The `client` fixture pre-issues a Bearer token. A real payment
    create should reach the service and return 201."""
    response = await client.post(
        "/payments/intents",
        json={
            "amount": 100,
            "currency": "usd",
            "idempotency_key": "auth-happy-path",
        },
    )
    assert response.status_code == 201


async def test_valid_key_bumps_last_used_at(
    no_auth_client, stub_stripe, session_factory, merchant_and_key
):
    """Successful verify must update APIKey.last_used_at on the matched row."""
    _, raw = merchant_and_key

    # Pre-condition: last_used_at is NULL right after creation.
    from sqlalchemy import select

    async with session_factory() as s:
        row = (
            await s.execute(
                select(APIKey).where(
                    APIKey.key_prefix == raw[8:16]  # chars after "gw_live_"
                )
            )
        ).scalar_one()
        assert row.last_used_at is None

    # Hit any authed endpoint with the valid Bearer.
    r = await no_auth_client.get(
        "/dashboard/balance",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 200

    async with session_factory() as s:
        row = (
            await s.execute(
                select(APIKey).where(APIKey.key_prefix == raw[8:16])
            )
        ).scalar_one()
        assert row.last_used_at is not None


# --- Revocation -------------------------------------------------------------


async def test_revoked_key_is_401(
    no_auth_client, stub_stripe, session_factory, merchant_and_key
):
    merchant_id, raw = merchant_and_key

    # Confirm baseline: the key works.
    r = await no_auth_client.get(
        "/dashboard/balance", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 200

    # Revoke it.
    from sqlalchemy import select

    async with session_factory() as s:
        row = (
            await s.execute(
                select(APIKey).where(APIKey.merchant_id == merchant_id)
            )
        ).scalar_one()
        await api_key_service.revoke_api_key(
            s, key_id=row.id, merchant_id=merchant_id
        )

    # Same Bearer, now rejected.
    r = await no_auth_client.get(
        "/dashboard/balance", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 401
    assert "revoked" in (r.json().get("detail") or "").lower()


async def test_revoked_key_rejected_even_when_hash_matches(
    session_factory, merchant_and_key
):
    """Directly exercises the service path: the hash of `raw` matches the row
    in DB. is_active=False alone must be enough to reject — no other state
    change should flip the verdict back to 'allowed'."""
    merchant_id, raw = merchant_and_key

    # Revoke the key (is_active → False, hash unchanged).
    from sqlalchemy import select

    async with session_factory() as s:
        row = (
            await s.execute(
                select(APIKey).where(APIKey.merchant_id == merchant_id)
            )
        ).scalar_one()
        await api_key_service.revoke_api_key(
            s, key_id=row.id, merchant_id=merchant_id
        )

        # Re-fetch and confirm: hash matches the raw key, but is_active is False.
        row = (
            await s.execute(
                select(APIKey).where(APIKey.merchant_id == merchant_id)
            )
        ).scalar_one()
        expected_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert row.key_hash == expected_hash  # the matching path is intact
        assert row.is_active is False

    # Verifier MUST raise even though the hash is a perfect match.
    async with session_factory() as s:
        with pytest.raises(UnauthorizedError) as exc:
            await api_key_service.verify_api_key(s, raw)
        assert "revoked" in str(exc.value).lower()


async def test_inactive_merchant_is_401(
    no_auth_client, stub_stripe, session_factory, merchant_and_key
):
    """Active key + inactive merchant → 401. Defence in depth."""
    merchant_id, raw = merchant_and_key

    async with session_factory() as s:
        m = await s.get(Merchant, merchant_id)
        m.is_active = False
        await s.commit()

    r = await no_auth_client.get(
        "/dashboard/balance", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 401


# --- Prefix collision -------------------------------------------------------


async def test_prefix_collision_resolves_to_correct_key(session_factory):
    """Two APIKey rows share the same `key_prefix` but belong to different
    merchants and have different hashes. verify_api_key must:
      * fetch both candidates by prefix
      * iterate, hash-compare each
      * return only the merchant whose key actually matches
      * never confuse one for the other
    """
    shared_prefix = "aa11aa11"
    raw_alice = "gw_live_" + shared_prefix + "aa" * 28  # 56 chars of body
    raw_bob = "gw_live_" + shared_prefix + "bb" * 28
    # Sanity: same lookup prefix, identical shape.
    assert raw_alice[8:16] == raw_bob[8:16] == shared_prefix
    assert len(raw_alice) == len(raw_bob) == 8 + 64
    # Sanity: different hashes.
    assert hashlib.sha256(raw_alice.encode()).hexdigest() != hashlib.sha256(
        raw_bob.encode()
    ).hexdigest()

    async with session_factory() as s:
        alice = Merchant(name="Alice", email="alice@example.com")
        bob = Merchant(name="Bob", email="bob@example.com")
        s.add_all([alice, bob])
        await s.commit()
        await s.refresh(alice)
        await s.refresh(bob)

        s.add_all(
            [
                APIKey(
                    merchant_id=alice.id,
                    key_prefix="aa11aa11",
                    key_hash=hashlib.sha256(raw_alice.encode()).hexdigest(),
                    name="alice-key",
                    is_active=True,
                ),
                APIKey(
                    merchant_id=bob.id,
                    key_prefix="aa11aa11",
                    key_hash=hashlib.sha256(raw_bob.encode()).hexdigest(),
                    name="bob-key",
                    is_active=True,
                ),
            ]
        )
        await s.commit()

    # Verify alice's raw key returns alice.
    async with session_factory() as s:
        merchant = await api_key_service.verify_api_key(s, raw_alice)
        assert merchant.email == "alice@example.com"

    # Verify bob's raw key returns bob — never confused for alice.
    async with session_factory() as s:
        merchant = await api_key_service.verify_api_key(s, raw_bob)
        assert merchant.email == "bob@example.com"

    # Verify a third key with the same prefix but a wrong body is rejected,
    # even though it shares the lookup prefix with both rows.
    raw_eve = "gw_live_" + shared_prefix + "ee" * 28
    assert raw_eve[8:16] == shared_prefix
    async with session_factory() as s:
        with pytest.raises(UnauthorizedError):
            await api_key_service.verify_api_key(s, raw_eve)
