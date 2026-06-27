import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class LedgerEntryDirection(str, enum.Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class LedgerEntry(Base):
    """One half of a double-entry transaction.

    Every payment event writes a balanced pair of rows sharing a transaction_id.
    The pair must sum to zero when debits are subtracted from credits.
    """

    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"le_{uuid.uuid4().hex}",
    )

    # Pair-identifier — both halves of a single transaction share this.
    transaction_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )

    account: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    direction: Mapped[LedgerEntryDirection] = mapped_column(
        SQLEnum(LedgerEntryDirection, name="ledger_entry_direction"),
        nullable=False,
    )

    # Always positive integers; sign comes from `direction`.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    payment_intent_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_intents.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    entry_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_ledger_account_currency", "account", "currency"),
    )
