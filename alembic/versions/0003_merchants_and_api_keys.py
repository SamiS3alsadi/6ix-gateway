"""merchants + api_keys + payment_intents.merchant_id

Revision ID: 0003_merchants_and_api_keys
Revises: 0002_reconciliation_run
Create Date: 2026-06-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_merchants_and_api_keys"
down_revision: Union[str, None] = "0002_reconciliation_run"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- merchants -----------------------------------------------------------
    op.create_table(
        "merchants",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
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
        sa.UniqueConstraint("email", name="uq_merchants_email"),
    )
    op.create_index("ix_merchants_email", "merchants", ["email"])

    # --- api_keys ------------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "merchant_id",
            sa.String(length=64),
            sa.ForeignKey("merchants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("key_prefix", sa.String(length=8), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_api_keys_merchant_id", "api_keys", ["merchant_id"])
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])

    # --- payment_intents.merchant_id -----------------------------------------
    # Nullable for now — existing rows have no merchant. Section 8 wires every
    # new create to the authenticated merchant. A later migration can backfill
    # and tighten to NOT NULL.
    op.add_column(
        "payment_intents",
        sa.Column(
            "merchant_id",
            sa.String(length=64),
            sa.ForeignKey("merchants.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_payment_intents_merchant_id",
        "payment_intents",
        ["merchant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_intents_merchant_id", table_name="payment_intents")
    op.drop_column("payment_intents", "merchant_id")

    op.drop_index("ix_api_keys_key_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_merchant_id", table_name="api_keys")
    op.drop_table("api_keys")

    op.drop_index("ix_merchants_email", table_name="merchants")
    op.drop_table("merchants")
