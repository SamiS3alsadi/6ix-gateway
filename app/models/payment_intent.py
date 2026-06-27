import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SQLEnum,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PaymentIntentStatus(str, enum.Enum):
    REQUIRES_PAYMENT_METHOD = "requires_payment_method"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    REQUIRES_ACTION = "requires_action"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"
    FAILED = "failed"


class PaymentIntent(Base):
    __tablename__ = "payment_intents"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"pi_local_{uuid.uuid4().hex}",
    )
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )

    # Amounts are integers in the smallest currency unit (cents).
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_received: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    amount_refunded: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    status: Mapped[PaymentIntentStatus] = mapped_column(
        SQLEnum(PaymentIntentStatus, name="payment_intent_status"),
        default=PaymentIntentStatus.REQUIRES_PAYMENT_METHOD,
        nullable=False,
        index=True,
    )

    customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    client_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    payment_metadata: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_payment_intents_customer_created", "customer_id", "created_at"),
    )
