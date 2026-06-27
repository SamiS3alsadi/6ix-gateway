from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.ledger import LedgerEntryDirection


class LedgerEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    transaction_id: str
    account: str
    direction: LedgerEntryDirection
    amount: int
    currency: str
    payment_intent_id: str | None
    description: str | None
    created_at: datetime


class BalanceRead(BaseModel):
    account: str
    currency: str
    balance: int = Field(..., description="Net balance in cents (credits - debits).")
