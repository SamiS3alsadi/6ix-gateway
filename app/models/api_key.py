import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class APIKey(Base):
    """A merchant's API credential.

    The raw key is only ever returned to the caller at creation time. We
    store:
      * `key_prefix` — first 8 chars of the random portion (after `gw_live_`),
        indexed for fast lookup. Not unique on its own — collisions are
        possible (birthday paradox kicks in around ~65k keys), so verification
        looks up by prefix, then compares hashes among the matches.
      * `key_hash` — SHA-256 hex digest of the full key. 64 chars fixed.
        Constant-time compared on verify.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"ak_{uuid.uuid4().hex}",
    )
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
