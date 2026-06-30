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


class AccountBalance(BaseModel):
    account: str
    balance: int = Field(..., description="credits - debits in cents")


class CurrencyBalance(BaseModel):
    currency: str
    net: int = Field(
        ...,
        description=(
            "Sum of credits minus debits across all accounts. In a balanced "
            "ledger this should always be 0 — any non-zero value indicates "
            "drift."
        ),
    )
    accounts: list[AccountBalance]


class BalanceResponse(BaseModel):
    currencies: list[CurrencyBalance]
