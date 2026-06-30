"""API key issuance and verification.

Key format:           gw_live_<64 random hex chars>     (72 chars total)
Stored prefix:        first 8 hex chars after `gw_live_` (selective index)
Stored hash:          sha256(full_key) hex             (64 chars)

The raw key is only ever returned to the caller at creation time. Verification
looks up by prefix, then constant-time compares the sha256 of the incoming
key against `key_hash` to defeat timing side channels.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIKeyNotFoundError, UnauthorizedError
from app.models.api_key import APIKey
from app.models.merchant import Merchant

KEY_PREFIX_LITERAL = "gw_live_"
KEY_PREFIX_INDEX_LEN = 8
KEY_RANDOM_BYTES = 32  # → 64 hex chars


# --- helpers ---------------------------------------------------------------


def generate_key() -> str:
    """Return a fresh raw API key. Caller is responsible for storing the
    prefix + hash and showing the plaintext to the user *exactly once*."""
    return f"{KEY_PREFIX_LITERAL}{secrets.token_hex(KEY_RANDOM_BYTES)}"


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _extract_lookup_prefix(raw_key: str) -> str | None:
    """Return the 8 hex chars right after the `gw_live_` literal.

    Returns None for anything that doesn't have the expected shape so callers
    can short-circuit to UnauthorizedError without a DB hit.
    """
    if not raw_key.startswith(KEY_PREFIX_LITERAL):
        return None
    body = raw_key[len(KEY_PREFIX_LITERAL):]
    if len(body) < KEY_PREFIX_INDEX_LEN:
        return None
    return body[:KEY_PREFIX_INDEX_LEN]


# --- mutations -------------------------------------------------------------


async def create_api_key(
    session: AsyncSession,
    *,
    merchant_id: str,
    name: str,
) -> tuple[APIKey, str]:
    """Issue a new key for a merchant. Returns (row, plaintext).

    The plaintext is only returned here — it cannot be recovered later.
    """
    raw = generate_key()
    prefix = _extract_lookup_prefix(raw)
    assert prefix is not None  # generate_key always yields a valid shape

    row = APIKey(
        merchant_id=merchant_id,
        key_prefix=prefix,
        key_hash=_hash_key(raw),
        name=name,
        is_active=True,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row, raw


async def revoke_api_key(
    session: AsyncSession,
    *,
    key_id: str,
    merchant_id: str,
) -> APIKey:
    """Soft-revoke (is_active=False). Raises APIKeyNotFoundError if the key
    doesn't exist OR belongs to a different merchant — same response so we
    don't leak which keys exist across tenants."""
    result = await session.execute(
        select(APIKey).where(
            APIKey.id == key_id,
            APIKey.merchant_id == merchant_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise APIKeyNotFoundError(detail=f"api key {key_id} not found")
    row.is_active = False
    await session.commit()
    await session.refresh(row)
    return row


# --- verification ---------------------------------------------------------


async def verify_api_key(session: AsyncSession, raw_key: str) -> Merchant:
    """Authenticate a raw API key and return its merchant.

    Raises UnauthorizedError if:
      - key is missing/malformed
      - prefix has no match
      - none of the matching rows has a hash that matches the incoming key
      - the matched key is revoked (is_active=False)
      - the owning merchant is inactive
    Updates last_used_at on success.
    """
    if not raw_key:
        raise UnauthorizedError(detail="missing api key")

    prefix = _extract_lookup_prefix(raw_key)
    if prefix is None:
        raise UnauthorizedError(detail="malformed api key")

    incoming_hash = _hash_key(raw_key)

    # Fetch all candidates with this prefix — typically 1, occasionally
    # several thanks to birthday-paradox collisions in 16^8.
    candidates = (
        await session.execute(
            select(APIKey).where(APIKey.key_prefix == prefix)
        )
    ).scalars().all()

    matched: APIKey | None = None
    for cand in candidates:
        # Constant-time compare so timing of "right prefix wrong hash" looks
        # identical to "right prefix right hash but different row".
        if hmac.compare_digest(cand.key_hash, incoming_hash):
            matched = cand
            break

    if matched is None:
        raise UnauthorizedError(detail="api key not recognised")
    if not matched.is_active:
        raise UnauthorizedError(detail="api key revoked")

    merchant = await session.get(Merchant, matched.merchant_id)
    if merchant is None or not merchant.is_active:
        raise UnauthorizedError(detail="merchant inactive")

    matched.last_used_at = datetime.now(timezone.utc)
    await session.commit()
    return merchant
