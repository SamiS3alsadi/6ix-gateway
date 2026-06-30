"""Admin-only merchant + API key management endpoints."""
import pytest

pytestmark = pytest.mark.asyncio

ADMIN = {"X-Admin-Key": "test-admin-key"}


# --- Auth gate ---------------------------------------------------------------


async def test_admin_endpoints_reject_missing_admin_key(no_auth_client):
    response = await no_auth_client.post(
        "/merchants", json={"name": "m", "email": "m@example.com"}
    )
    assert response.status_code == 401


async def test_admin_endpoints_reject_wrong_admin_key(no_auth_client):
    response = await no_auth_client.post(
        "/merchants",
        json={"name": "m", "email": "m@example.com"},
        headers={"X-Admin-Key": "wrong-key"},
    )
    assert response.status_code == 401


# --- Create merchant ---------------------------------------------------------


async def test_create_merchant_happy_path(no_auth_client):
    response = await no_auth_client.post(
        "/merchants",
        json={"name": "Acme Co", "email": "acme@example.com"},
        headers=ADMIN,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Acme Co"
    assert body["email"] == "acme@example.com"
    assert body["is_active"] is True
    assert body["id"].startswith("mer_")


async def test_create_merchant_duplicate_email(no_auth_client):
    payload = {"name": "Acme Co", "email": "dup@example.com"}
    first = await no_auth_client.post("/merchants", json=payload, headers=ADMIN)
    assert first.status_code == 201

    second = await no_auth_client.post("/merchants", json=payload, headers=ADMIN)
    assert second.status_code == 409
    assert second.json()["error_code"] == "IDEMPOTENCY_CONFLICT"


async def test_create_merchant_email_lowercased(no_auth_client):
    response = await no_auth_client.post(
        "/merchants",
        json={"name": "Mixed", "email": "Mixed.Case@Example.com"},
        headers=ADMIN,
    )
    assert response.status_code == 201
    assert response.json()["email"] == "mixed.case@example.com"


# --- Issue / list / revoke api keys -----------------------------------------


async def _create_merchant(no_auth_client, email="ak@example.com") -> str:
    r = await no_auth_client.post(
        "/merchants",
        json={"name": "Key Owner", "email": email},
        headers=ADMIN,
    )
    return r.json()["id"]


async def test_issue_api_key_returns_plaintext_once(no_auth_client):
    mid = await _create_merchant(no_auth_client)
    response = await no_auth_client.post(
        f"/merchants/{mid}/api-keys",
        json={"name": "production"},
        headers=ADMIN,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["key"].startswith("gw_live_")
    assert len(body["key"]) == 8 + 64  # "gw_live_" + 64 hex chars
    assert body["key_prefix"] == body["key"][8:16]  # prefix after literal
    assert body["name"] == "production"
    assert body["is_active"] is True

    # Plaintext is NOT in the list response.
    listing = await no_auth_client.get(
        f"/merchants/{mid}/api-keys", headers=ADMIN
    )
    assert listing.status_code == 200
    items = listing.json()
    assert len(items) == 1
    assert "key" not in items[0]
    assert "key_hash" not in items[0]


async def test_issue_api_key_for_unknown_merchant_404(no_auth_client):
    response = await no_auth_client.post(
        "/merchants/mer_nonexistent/api-keys",
        json={"name": "test"},
        headers=ADMIN,
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "MERCHANT_NOT_FOUND"


async def test_list_api_keys_includes_revoked(no_auth_client):
    mid = await _create_merchant(no_auth_client, email="list@example.com")
    k1 = await no_auth_client.post(
        f"/merchants/{mid}/api-keys", json={"name": "k1"}, headers=ADMIN
    )
    await no_auth_client.post(
        f"/merchants/{mid}/api-keys", json={"name": "k2"}, headers=ADMIN
    )

    # Revoke k1
    await no_auth_client.delete(
        f"/merchants/{mid}/api-keys/{k1.json()['id']}", headers=ADMIN
    )

    listing = await no_auth_client.get(
        f"/merchants/{mid}/api-keys", headers=ADMIN
    )
    body = listing.json()
    assert len(body) == 2
    by_name = {k["name"]: k for k in body}
    assert by_name["k1"]["is_active"] is False  # revoked
    assert by_name["k2"]["is_active"] is True


async def test_revoke_api_key_unknown_id_404(no_auth_client):
    mid = await _create_merchant(no_auth_client, email="rev@example.com")
    response = await no_auth_client.delete(
        f"/merchants/{mid}/api-keys/ak_nonexistent", headers=ADMIN
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "API_KEY_NOT_FOUND"


async def test_revoke_api_key_wrong_merchant_404(no_auth_client):
    mid_a = await _create_merchant(no_auth_client, email="a@example.com")
    mid_b = await _create_merchant(no_auth_client, email="b@example.com")
    key_a = await no_auth_client.post(
        f"/merchants/{mid_a}/api-keys", json={"name": "a"}, headers=ADMIN
    )

    # Merchant B tries to revoke merchant A's key — should 404, not 403.
    response = await no_auth_client.delete(
        f"/merchants/{mid_b}/api-keys/{key_a.json()['id']}", headers=ADMIN
    )
    assert response.status_code == 404
