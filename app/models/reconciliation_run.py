import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ReconciliationRun(Base):
    """One reconciliation pass — compares Stripe's day-of-record against ours.

    Written by the workers/reconciliation.py job. The `mismatches` JSON column
    captures per-payment-intent discrepancies (missing on either side, or
    amount drift) so an operator can drill into the cause without replaying.
    """

    __tablename__ = "reconciliation_runs"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"rr_{uuid.uuid4().hex}",
    )
    run_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    total_stripe: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_internal: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    mismatches_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    mismatches: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
