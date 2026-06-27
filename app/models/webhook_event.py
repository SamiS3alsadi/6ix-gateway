from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class WebhookEvent(Base):
    """Persisted Stripe webhook events for idempotent processing + audit."""

    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    api_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
