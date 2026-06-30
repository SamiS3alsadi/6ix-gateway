"""reconciliation_runs table

Revision ID: 0002_reconciliation_run
Revises: 0001_initial
Create Date: 2026-06-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_reconciliation_run"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("total_stripe", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "total_internal", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "mismatches_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "mismatches",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_reconciliation_runs_run_date",
        "reconciliation_runs",
        ["run_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reconciliation_runs_run_date", table_name="reconciliation_runs"
    )
    op.drop_table("reconciliation_runs")
