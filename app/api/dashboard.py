from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import require_dashboard_api_key
from app.models.payment_intent import PaymentIntent, PaymentIntentStatus
from app.schemas.ledger import BalanceRead, LedgerEntryRead
from app.schemas.payment import PaymentIntentRead
from app.services import ledger as ledger_service

router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_dashboard_api_key)],
)


@router.get("/intents", response_model=list[PaymentIntentRead])
async def list_intents(
    limit: int = Query(default=50, ge=1, le=500),
    status: PaymentIntentStatus | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[PaymentIntentRead]:
    stmt = select(PaymentIntent).order_by(PaymentIntent.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(PaymentIntent.status == status)
    result = await db.execute(stmt)
    return [PaymentIntentRead.model_validate(row) for row in result.scalars().all()]


@router.get("/balance", response_model=BalanceRead)
async def get_balance(
    account: str,
    currency: str,
    db: AsyncSession = Depends(get_db),
) -> BalanceRead:
    balance = await ledger_service.get_balance(db, account=account, currency=currency)
    return BalanceRead(account=account, currency=currency.lower(), balance=balance)


@router.get(
    "/intents/{intent_id}/ledger", response_model=list[LedgerEntryRead]
)
async def list_intent_ledger(
    intent_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[LedgerEntryRead]:
    entries = await ledger_service.list_entries_for_intent(
        db, payment_intent_id=intent_id
    )
    return [LedgerEntryRead.model_validate(e) for e in entries]


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db)) -> dict:
    stmt = select(PaymentIntent.status, func.count(), func.coalesce(func.sum(PaymentIntent.amount), 0)).group_by(
        PaymentIntent.status
    )
    result = await db.execute(stmt)
    return {
        "by_status": [
            {"status": row[0].value, "count": int(row[1]), "amount_sum": int(row[2])}
            for row in result.all()
        ]
    }
