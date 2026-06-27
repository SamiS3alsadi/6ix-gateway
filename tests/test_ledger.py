import pytest

from app.models.ledger import LedgerEntryDirection
from app.services.ledger import (
    LedgerError,
    LedgerLeg,
    get_balance,
    record_transaction,
)

pytestmark = pytest.mark.asyncio


async def test_balanced_transaction_records_entries(session):
    entries = await record_transaction(
        session,
        legs=[
            LedgerLeg("customer_pending:cus_1", LedgerEntryDirection.DEBIT, 1000),
            LedgerLeg("payments_authorised", LedgerEntryDirection.CREDIT, 1000),
        ],
        currency="usd",
        description="test charge",
    )
    await session.commit()
    assert len(entries) == 2
    assert all(e.transaction_id == entries[0].transaction_id for e in entries)


async def test_unbalanced_transaction_rejected(session):
    with pytest.raises(LedgerError):
        await record_transaction(
            session,
            legs=[
                LedgerLeg("a", LedgerEntryDirection.DEBIT, 100),
                LedgerLeg("b", LedgerEntryDirection.CREDIT, 50),
            ],
            currency="usd",
        )


async def test_single_leg_rejected(session):
    with pytest.raises(LedgerError):
        await record_transaction(
            session,
            legs=[LedgerLeg("a", LedgerEntryDirection.DEBIT, 100)],
            currency="usd",
        )


async def test_negative_amount_rejected(session):
    with pytest.raises(LedgerError):
        await record_transaction(
            session,
            legs=[
                LedgerLeg("a", LedgerEntryDirection.DEBIT, -100),
                LedgerLeg("b", LedgerEntryDirection.CREDIT, -100),
            ],
            currency="usd",
        )


async def test_get_balance_after_multiple_transactions(session):
    # +1000 to payments_authorised
    await record_transaction(
        session,
        legs=[
            LedgerLeg("customer:1", LedgerEntryDirection.DEBIT, 1000),
            LedgerLeg("payments_authorised", LedgerEntryDirection.CREDIT, 1000),
        ],
        currency="usd",
    )
    # -300 from payments_authorised (partial cancel)
    await record_transaction(
        session,
        legs=[
            LedgerLeg("payments_authorised", LedgerEntryDirection.DEBIT, 300),
            LedgerLeg("customer:1", LedgerEntryDirection.CREDIT, 300),
        ],
        currency="usd",
    )
    await session.commit()

    balance = await get_balance(session, account="payments_authorised", currency="usd")
    assert balance == 700


async def test_balance_segregated_by_currency(session):
    await record_transaction(
        session,
        legs=[
            LedgerLeg("a", LedgerEntryDirection.DEBIT, 500),
            LedgerLeg("revenue", LedgerEntryDirection.CREDIT, 500),
        ],
        currency="usd",
    )
    await record_transaction(
        session,
        legs=[
            LedgerLeg("a", LedgerEntryDirection.DEBIT, 1200),
            LedgerLeg("revenue", LedgerEntryDirection.CREDIT, 1200),
        ],
        currency="eur",
    )
    await session.commit()

    assert await get_balance(session, account="revenue", currency="usd") == 500
    assert await get_balance(session, account="revenue", currency="eur") == 1200
