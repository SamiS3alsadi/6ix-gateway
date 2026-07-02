from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models.checkout_session import CheckoutSessionStatus


class CheckoutSessionCreate(BaseModel):
    amount: int = Field(
        ..., ge=1, description="Amount in the smallest currency unit (cents)."
    )
    currency: str = Field(..., min_length=3, max_length=3)
    idempotency_key: str = Field(..., min_length=8, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    success_url: str | None = Field(default=None, max_length=2048)

    @field_validator("currency")
    @classmethod
    def _lower_currency(cls, v: str) -> str:
        return v.lower()


class CheckoutSessionRead(BaseModel):
    """Response shape for the merchant-facing API.

    `checkout_url` is a relative path — the caller stitches on its host to
    hand a full link to the customer. Keeping it relative sidesteps needing
    the app to know its public origin (which varies between Railway preview
    URLs, custom domains, dev localhost, etc.).
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    merchant_id: str
    payment_intent_id: str | None
    amount: int
    currency: str
    description: str | None
    status: CheckoutSessionStatus
    success_url: str | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def checkout_url(self) -> str:
        return f"/checkout/{self.id}"
