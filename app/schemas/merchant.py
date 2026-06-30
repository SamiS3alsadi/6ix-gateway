from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MerchantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=3, max_length=255)


class MerchantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str
    is_active: bool
    created_at: datetime


class APIKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class APIKeyRead(BaseModel):
    """Safe-to-list shape. Never includes the hash or plaintext."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    merchant_id: str
    key_prefix: str
    name: str
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime


class APIKeyIssued(APIKeyRead):
    """Returned exactly once at creation — includes the plaintext key."""

    key: str = Field(
        ...,
        description=(
            "Full raw API key. Shown here only. The server keeps a SHA-256 "
            "hash and cannot recover this value later."
        ),
    )
