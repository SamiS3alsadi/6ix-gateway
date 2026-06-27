# 6ix-gateway — Project Rules

A FastAPI + Stripe payment gateway with a double-entry ledger.

## Stack
- Python 3.11
- FastAPI
- SQLAlchemy 2.0 (async)
- Alembic
- Stripe (server-side via stripe-python; client-side via Stripe.js)
- PostgreSQL

## Non-negotiable rules

1. **Money is integers.** All monetary amounts are stored and passed as integers
   in the smallest currency unit (cents). Never floats. Never decimals at the
   API boundary. Currency is stored separately as an ISO-4217 string.

2. **Every payment mutation requires an `idempotency_key`.** Create, confirm,
   cancel, refund — all must take a client-provided key and short-circuit on
   replay. Keys are forwarded to Stripe as the `Idempotency-Key` header.

3. **Webhooks are the source of truth for payment status.** Never poll Stripe
   to discover state transitions. The webhook handler is the only writer that
   advances `PaymentIntent.status` past `requires_action`.

4. **Raw card data never touches our server.** Card number, CVV, expiry — none
   of it. The browser tokenizes via Stripe.js and we only ever see the
   resulting `payment_method` id or `payment_intent` client secret.

5. **Async everywhere.** All I/O is `async def` + `await`. No sync DB sessions,
   no blocking HTTP calls. Stripe SDK calls go through a thread offload.

6. **Pydantic v2 for all schemas.** Request and response models live in
   `app/schemas/`. Use `model_config = ConfigDict(from_attributes=True)` for
   ORM-mode read models.

7. **Never log card numbers, CVVs, or PANs.** If a field could carry PAN data,
   it must be redacted before any log line. Stripe object ids (`pi_...`,
   `pm_...`, `ch_...`) are safe to log.

## Double-entry ledger

Every state-changing payment event writes a balanced pair of rows to the
`ledger_entries` table: one debit and one credit, summing to zero. The
`record_transaction` service is the only writer; it enforces the invariant in
the same DB transaction that records the entries.

## Layout

- `app/api/` — routers only, no business logic
- `app/core/` — config, db, security
- `app/models/` — SQLAlchemy ORM models
- `app/schemas/` — Pydantic request/response models
- `app/services/` — business logic, Stripe interactions, ledger writes
- `app/workers/` — background jobs (reconciliation)
- `tests/` — pytest, async
- `alembic/` — migrations
