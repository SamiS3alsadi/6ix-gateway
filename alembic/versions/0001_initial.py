"""initial schema — payment_intents, ledger_entries, webhook_events

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# create_type=False — we create the enums explicitly with checkfirst=True so
# the implicit CREATE TYPE that op.create_table would emit doesn't fire twice
# and raise DuplicateObjectError on re-runs / interrupted migrations.
payment_intent_status = postgresql.ENUM(
    "requires_payment_method",
    "requires_confirmation",
    "requires_action",
    "processing",
    "succeeded",
    "canceled",
    "failed",
    name="payment_intent_status",
    create_type=False,
)

ledger_entry_direction = postgresql.ENUM(
    "debit",
    "credit",
    name="ledger_entry_direction",
    create_type=False,
)


def upgrade() -> None:
    payment_intent_status.create(op.get_bind(), checkfirst=True)
    ledger_entry_direction.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "payment_intents",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "stripe_payment_intent_id", sa.String(length=255), unique=True, nullable=True
        ),
        sa.Column(
            "idempotency_key", sa.String(length=255), unique=True, nullable=False
        ),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount_received", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("amount_refunded", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            payment_intent_status,
            nullable=False,
            server_default="requires_payment_method",
        ),
        sa.Column("customer_id", sa.String(length=255), nullable=True),
        sa.Column("description", sa.String(length=1024), nullable=True),
        sa.Column("client_secret", sa.String(length=512), nullable=True),
        sa.Column("last_error", sa.String(length=2048), nullable=True),
        sa.Column(
            "payment_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
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
    )
    op.create_index(
        "ix_payment_intents_stripe_payment_intent_id",
        "payment_intents",
        ["stripe_payment_intent_id"],
    )
    op.create_index(
        "ix_payment_intents_idempotency_key", "payment_intents", ["idempotency_key"]
    )
    op.create_index("ix_payment_intents_status", "payment_intents", ["status"])
    op.create_index(
        "ix_payment_intents_customer_created",
        "payment_intents",
        ["customer_id", "created_at"],
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("account", sa.String(length=128), nullable=False),
        sa.Column("direction", ledger_entry_direction, nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "payment_intent_id",
            sa.String(length=64),
            sa.ForeignKey("payment_intents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column(
            "entry_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_ledger_entries_transaction_id", "ledger_entries", ["transaction_id"]
    )
    op.create_index("ix_ledger_entries_account", "ledger_entries", ["account"])
    op.create_index(
        "ix_ledger_entries_payment_intent_id", "ledger_entries", ["payment_intent_id"]
    )
    op.create_index(
        "ix_ledger_account_currency", "ledger_entries", ["account", "currency"]
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("type", sa.String(length=255), nullable=False),
        sa.Column("api_version", sa.String(length=32), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_error", sa.String(length=2048), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_webhook_events_type", "webhook_events", ["type"])


def downgrade() -> None:
    op.drop_index("ix_webhook_events_type", table_name="webhook_events")
    op.drop_table("webhook_events")

    op.drop_index("ix_ledger_account_currency", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_payment_intent_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_account", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_transaction_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")

    op.drop_index(
        "ix_payment_intents_customer_created", table_name="payment_intents"
    )
    op.drop_index("ix_payment_intents_status", table_name="payment_intents")
    op.drop_index(
        "ix_payment_intents_idempotency_key", table_name="payment_intents"
    )
    op.drop_index(
        "ix_payment_intents_stripe_payment_intent_id", table_name="payment_intents"
    )
    op.drop_table("payment_intents")

    bind = op.get_bind()
    ledger_entry_direction.drop(bind, checkfirst=True)
    payment_intent_status.drop(bind, checkfirst=True)
