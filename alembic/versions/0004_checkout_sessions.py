"""checkout_sessions table

Revision ID: 0004_checkout_sessions
Revises: 0003_merchants_and_api_keys
Create Date: 2026-07-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_checkout_sessions"
down_revision: Union[str, None] = "0003_merchants_and_api_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# create_type=False — we create the enum explicitly with checkfirst=True so
# the implicit CREATE TYPE that op.create_table would emit doesn't fire
# twice and raise DuplicateObjectError on re-runs / interrupted migrations
# (same pattern as 0001_initial).
checkout_session_status = postgresql.ENUM(
    "open",
    "completed",
    "expired",
    name="checkout_session_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    checkout_session_status.create(bind, checkfirst=True)

    op.create_table(
        "checkout_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "merchant_id",
            sa.String(length=64),
            sa.ForeignKey("merchants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "payment_intent_id",
            sa.String(length=64),
            sa.ForeignKey("payment_intents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=True),
        sa.Column(
            "status",
            checkout_session_status,
            nullable=False,
            server_default="open",
        ),
        sa.Column("success_url", sa.String(length=2048), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_checkout_sessions_merchant_id",
        "checkout_sessions",
        ["merchant_id"],
    )
    op.create_index(
        "ix_checkout_sessions_payment_intent_id",
        "checkout_sessions",
        ["payment_intent_id"],
    )
    op.create_index(
        "ix_checkout_sessions_status",
        "checkout_sessions",
        ["status"],
    )
    op.create_index(
        "ix_checkout_sessions_expires_at",
        "checkout_sessions",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_checkout_sessions_expires_at", table_name="checkout_sessions")
    op.drop_index("ix_checkout_sessions_status", table_name="checkout_sessions")
    op.drop_index(
        "ix_checkout_sessions_payment_intent_id",
        table_name="checkout_sessions",
    )
    op.drop_index(
        "ix_checkout_sessions_merchant_id",
        table_name="checkout_sessions",
    )
    op.drop_table("checkout_sessions")

    checkout_session_status.drop(op.get_bind(), checkfirst=True)
