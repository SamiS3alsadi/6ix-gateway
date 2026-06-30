from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.payment_intent import PaymentIntentStatus
from app.schemas.ledger import LedgerEntryRead


class PaymentIntentCreate(BaseModel):
    amount: int = Field(..., ge=1, description="Amount in smallest currency unit (cents).")
    currency: str = Field(..., min_length=3, max_length=3)
    idempotency_key: str = Field(..., min_length=8, max_length=255)
    customer_id: str | None = None
    description: str | None = Field(default=None, max_length=1024)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _lower_currency(cls, v: str) -> str:
        return v.lower()


class PaymentIntentConfirm(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=255)
    payment_method_id: str = Field(..., min_length=1)


class PaymentIntentCancel(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=255)
    reason: str | None = Field(default=None, max_length=255)


class PaymentIntentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    stripe_payment_intent_id: str | None
    amount: int
    currency: str
    amount_received: int
    amount_refunded: int
    status: PaymentIntentStatus
    customer_id: str | None
    description: str | None
    client_secret: str | None
    created_at: datetime
    updated_at: datetime


class RefundCreate(BaseModel):
    """Refund request body. payment_intent_id comes from the URL path."""

    amount: int | None = Field(
        default=None,
        ge=1,
        description="Partial amount in cents. Omit for a full refund.",
    )
    idempotency_key: str = Field(..., min_length=8, max_length=255)
    reason: str | None = Field(default=None, max_length=255)


class RefundRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    payment_intent_id: str
    amount: int
    currency: str
    status: str
    stripe_refund_id: str | None
    created_at: datetime


class PaginatedTransactions(BaseModel):
    items: list[PaymentIntentRead]
    page: int
    page_size: int
    total: int


class TransactionDetail(PaymentIntentRead):
    """A payment intent with its full ledger trail included."""

    ledger_entries: list[LedgerEntryRead] = Field(default_factory=list)
