import enum
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class CheckoutSessionStatus(str, enum.Enum):
    OPEN = "open"
    COMPLETED = "completed"
    EXPIRED = "expired"


def _default_expires_at() -> datetime:
    """24-hour window from creation. Public checkout links go stale after
    a day so an abandoned link can't be paid weeks later against a stale
    price. Enforced at read-time by `get_open_session` and at write-time
    by the webhook that flips OPEN → COMPLETED."""
    return datetime.now(timezone.utc) + timedelta(hours=24)


class CheckoutSession(Base):
    """A merchant-issued hosted-checkout link.

    Analogous to `stripe.checkout.Session` — the merchant creates one via
    the authed API, gets back a public URL, and the customer follows that
    URL to a payment page that already knows the amount, currency and
    description. The PaymentIntent is created eagerly at session-creation
    time so the customer's page load is a pure read + Stripe.confirmCardPayment
    round trip.
    """

    __tablename__ = "checkout_sessions"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"cs_{uuid.uuid4().hex}",
    )

    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Eagerly linked at create-time. Nullable so backfills / tests can insert
    # a session without a PI, but the public API always populates it.
    payment_intent_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_intents.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # values_callable — SQLAlchemy defaults to serializing enum members by
    # their Python name (uppercase). The Postgres enum is created with
    # lowercase values, so we force value-based serialization to match.
    status: Mapped[CheckoutSessionStatus] = mapped_column(
        SQLEnum(
            CheckoutSessionStatus,
            name="checkout_session_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=CheckoutSessionStatus.OPEN,
        nullable=False,
        index=True,
    )

    success_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_default_expires_at,
        nullable=False,
        index=True,
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
