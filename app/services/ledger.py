"""Double-entry ledger service.

Every state change writes a balanced pair of entries (debit + credit) sharing a
transaction id. Single writer enforces the zero-sum invariant inside the same
DB transaction that records the rows.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger import LedgerEntry, LedgerEntryDirection


@dataclass(slots=True)
class LedgerLeg:
    account: str
    direction: LedgerEntryDirection
    amount: int  # always positive cents


class LedgerError(ValueError):
    """Raised when ledger invariants are violated."""


async def record_transaction(
    session: AsyncSession,
    *,
    legs: Sequence[LedgerLeg],
    currency: str,
    payment_intent_id: str | None = None,
    description: str | None = None,
    metadata: dict | None = None,
    transaction_id: str | None = None,
) -> list[LedgerEntry]:
    """Record a balanced multi-leg transaction.

    The sum of credits must equal the sum of debits. Amounts are integers
    in the smallest currency unit. Raises LedgerError if unbalanced.
    """
    if len(legs) < 2:
        raise LedgerError("A transaction requires at least two legs.")

    debits = sum(leg.amount for leg in legs if leg.direction == LedgerEntryDirection.DEBIT)
    credits = sum(leg.amount for leg in legs if leg.direction == LedgerEntryDirection.CREDIT)
    if debits != credits:
        raise LedgerError(
            f"Unbalanced transaction: debits={debits} credits={credits}"
        )
    if any(leg.amount <= 0 for leg in legs):
        raise LedgerError("All leg amounts must be positive integers.")

    txn_id = transaction_id or f"tx_{uuid.uuid4().hex}"
    currency_lower = currency.lower()

    entries = [
        LedgerEntry(
            transaction_id=txn_id,
            account=leg.account,
            direction=leg.direction,
            amount=leg.amount,
            currency=currency_lower,
            payment_intent_id=payment_intent_id,
            description=description,
            entry_metadata=metadata or {},
        )
        for leg in legs
    ]
    session.add_all(entries)
    await session.flush()
    return entries


async def get_balance(
    session: AsyncSession, *, account: str, currency: str
) -> int:
    """Net balance in cents = sum(credits) - sum(debits)."""
    stmt = select(
        func.coalesce(
            func.sum(
                case(
                    (LedgerEntry.direction == LedgerEntryDirection.CREDIT, LedgerEntry.amount),
                    else_=-LedgerEntry.amount,
                )
            ),
            0,
        )
    ).where(
        LedgerEntry.account == account,
        LedgerEntry.currency == currency.lower(),
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def list_entries_for_intent(
    session: AsyncSession, *, payment_intent_id: str
) -> list[LedgerEntry]:
    stmt = (
        select(LedgerEntry)
        .where(LedgerEntry.payment_intent_id == payment_intent_id)
        .order_by(LedgerEntry.created_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_balances_by_currency(
    session: AsyncSession,
) -> dict[str, dict[str, int]]:
    """All-accounts breakdown grouped by currency.

    Returns: {currency: {account: balance_in_cents}}. A balanced ledger has
    sum(balances) == 0 within every currency. Used by the dashboard /balance
    endpoint as both a position view and a drift sentinel.
    """
    signed = case(
        (LedgerEntry.direction == LedgerEntryDirection.CREDIT, LedgerEntry.amount),
        else_=-LedgerEntry.amount,
    )
    stmt = (
        select(
            LedgerEntry.currency,
            LedgerEntry.account,
            func.coalesce(func.sum(signed), 0).label("balance"),
        )
        .group_by(LedgerEntry.currency, LedgerEntry.account)
        .order_by(LedgerEntry.currency.asc(), LedgerEntry.account.asc())
    )
    result = await session.execute(stmt)
    grouped: dict[str, dict[str, int]] = {}
    for currency, account, balance in result.all():
        grouped.setdefault(currency, {})[account] = int(balance)
    return grouped
