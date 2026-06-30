from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.db import get_db
from app.core.errors import PaymentNotFoundError
from app.models.merchant import Merchant
from app.models.payment_intent import PaymentIntent, PaymentIntentStatus
from app.schemas.ledger import (
    AccountBalance,
    BalanceResponse,
    CurrencyBalance,
    LedgerEntryRead,
)
from app.schemas.payment import (
    PaginatedTransactions,
    PaymentIntentRead,
    TransactionDetail,
)
from app.services import ledger as ledger_service
from app.services import payment as payment_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    merchant: Merchant = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> BalanceResponse:
    """Per-currency, per-account position for the **authenticated merchant**.

    The double-entry ledger across all merchants always nets to 0; this view
    restricts to entries whose underlying payment_intent belongs to the
    authenticated merchant, so per-currency `net` is the merchant's own
    drift sentinel.
    """
    grouped = await ledger_service.get_balances_by_currency(
        db, merchant_id=merchant.id
    )
    currencies = [
        CurrencyBalance(
            currency=currency,
            net=sum(accounts.values()),
            accounts=[
                AccountBalance(account=acct, balance=bal)
                for acct, bal in sorted(accounts.items())
            ],
        )
        for currency, accounts in sorted(grouped.items())
    ]
    return BalanceResponse(currencies=currencies)


@router.get("/transactions", response_model=PaginatedTransactions)
async def list_transactions(
    merchant: Merchant = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    status_filter: PaymentIntentStatus | None = Query(default=None, alias="status"),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
) -> PaginatedTransactions:
    base = select(PaymentIntent).where(PaymentIntent.merchant_id == merchant.id)
    if status_filter is not None:
        base = base.where(PaymentIntent.status == status_filter)
    if currency is not None:
        base = base.where(PaymentIntent.currency == currency.lower())

    total_stmt = select(func.count()).select_from(base.subquery())
    total = int((await db.execute(total_stmt)).scalar_one())

    offset = (page - 1) * page_size
    rows = (
        await db.execute(
            base.order_by(PaymentIntent.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).scalars().all()

    return PaginatedTransactions(
        items=[PaymentIntentRead.model_validate(r) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/transactions/{intent_id}", response_model=TransactionDetail)
async def get_transaction(
    intent_id: str,
    merchant: Merchant = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> TransactionDetail:
    intent = await payment_service.get_by_id(db, intent_id)
    # Same 404 for "doesn't exist" and "belongs to another merchant" — don't
    # leak cross-tenant intent existence via response code differences.
    if intent is None or intent.merchant_id != merchant.id:
        raise PaymentNotFoundError(detail=f"PaymentIntent {intent_id} not found")

    entries = await ledger_service.list_entries_for_intent(
        db, payment_intent_id=intent_id
    )
    base = PaymentIntentRead.model_validate(intent).model_dump()
    return TransactionDetail(
        **base,
        ledger_entries=[LedgerEntryRead.model_validate(e) for e in entries],
    )
