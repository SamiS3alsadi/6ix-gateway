import logging
from contextlib import asynccontextmanager
from html import escape as _html_escape
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import (
    checkout_sessions,
    dashboard,
    merchants,
    payments,
    refunds,
    webhooks,
)
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AppException, ErrorCode, ErrorResponse
from app.models.checkout_session import CheckoutSessionStatus
from app.models.payment_intent import PaymentIntent
from app.services import checkout_session as cs_service

_STATIC_DIR = Path(__file__).parent / "app" / "static"

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting %s in %s mode", settings.app_name, settings.app_env)
    logger.info(
        "stripe webhook secret loaded: prefix=%s",
        settings.stripe_webhook_secret_prefix,
    )
    yield
    logger.info("shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)


# --- Exception handlers ----------------------------------------------------
# Every error response renders as ErrorResponse:
#   {"error_code": "...", "message": "...", "detail": "..."}


def _render(http_status: int, code: ErrorCode, message: str, detail: str | None) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content=ErrorResponse(
            error_code=code.value, message=message, detail=detail
        ).model_dump(),
    )


@app.exception_handler(AppException)
async def handle_app_exception(request: Request, exc: AppException) -> JSONResponse:
    return _render(exc.http_status, exc.code, exc.message, exc.detail)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _render(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        ErrorCode.VALIDATION_ERROR,
        "request body failed validation",
        str(exc.errors()),
    )


@app.exception_handler(HTTPException)
async def handle_http_exception(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Wrap any vanilla HTTPException (e.g. from FastAPI internals) into our shape.

    Picks an error code from the status code so the response is consistent
    with everything else the API emits.
    """
    code = {
        status.HTTP_401_UNAUTHORIZED: ErrorCode.UNAUTHORIZED,
        status.HTTP_404_NOT_FOUND: ErrorCode.PAYMENT_NOT_FOUND,
        status.HTTP_422_UNPROCESSABLE_ENTITY: ErrorCode.VALIDATION_ERROR,
    }.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    return _render(
        exc.status_code,
        code,
        message=str(exc.detail) if exc.detail else "error",
        detail=str(exc.detail) if exc.detail else None,
    )


@app.exception_handler(Exception)
async def handle_unhandled_exception(
    request: Request, exc: Exception
) -> JSONResponse:
    logger.exception("unhandled exception on %s %s", request.method, request.url.path)
    return _render(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        ErrorCode.INTERNAL_ERROR,
        "internal server error",
        detail=None,  # do not leak internals
    )


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict:
    return {"status": "ok"}


app.include_router(payments.router)
app.include_router(webhooks.router)
app.include_router(refunds.router)
app.include_router(dashboard.router)
app.include_router(merchants.router)
app.include_router(checkout_sessions.router)


# --- Static checkout UI ----------------------------------------------------

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/checkout", include_in_schema=False)
async def checkout_page() -> FileResponse:
    return FileResponse(_STATIC_DIR / "checkout.html", media_type="text/html")


# --- Public hosted-checkout page (no auth) ---------------------------------
# NOTE on template substitution: we use plain str.replace with {{TOKEN}}
# markers rather than a template engine. Every user-controlled value is
# routed through _html_escape / _url_escape before substitution — never
# put a raw value into the HTML.


def _format_amount(amount_cents: int, currency: str) -> str:
    """Human-readable amount for display. USD-shaped formatting; other
    currencies fall back to `<CODE> 12.34`. Fine for a status page."""
    major = amount_cents / 100
    upper = currency.upper()
    if upper == "USD":
        return f"${major:,.2f}"
    return f"{upper} {major:,.2f}"


def _url_escape(url: str) -> str:
    """Attribute-safe URL. Also drops anything that isn't http(s) to
    kill `javascript:` and other scheme-based XSS vectors."""
    if not url:
        return ""
    lower = url.lower().lstrip()
    if not (lower.startswith("http://") or lower.startswith("https://")):
        return ""
    return _html_escape(url, quote=True)


def _render_open_page(template: str, *, pk: str, client_secret: str,
                      amount_cents: int, currency: str,
                      description: str | None,
                      success_url: str | None) -> str:
    amount_display = _format_amount(amount_cents, currency)
    desc = _html_escape(description or "Payment")
    subs = {
        "{{PK}}": _html_escape(pk, quote=True),
        "{{CLIENT_SECRET}}": _html_escape(client_secret, quote=True),
        "{{SUCCESS_URL}}": _url_escape(success_url or ""),
        "{{TITLE}}": f"{amount_display} — 6ix Gateway",
        "{{AMOUNT_DISPLAY}}": _html_escape(amount_display),
        "{{DESCRIPTION}}": desc,
    }
    out = template
    for token, value in subs.items():
        out = out.replace(token, value)
    return out


def _render_message_page(template: str, *, title: str, heading: str,
                         body: str, icon_glyph: str, icon_class: str,
                         action_html: str = "") -> str:
    subs = {
        "{{TITLE}}": _html_escape(title),
        "{{HEADING}}": _html_escape(heading),
        "{{BODY}}": _html_escape(body),
        "{{ICON_GLYPH}}": icon_glyph,   # trusted, hardcoded
        "{{ICON_CLASS}}": icon_class,   # trusted, hardcoded
        "{{ACTION_HTML}}": action_html, # trusted, built by _render_action
    }
    out = template
    for token, value in subs.items():
        out = out.replace(token, value)
    return out


def _render_action_link(success_url: str | None) -> str:
    safe = _url_escape(success_url or "")
    if not safe:
        return ""
    return f'<a class="btn" href="{safe}">Continue</a>'


@app.get("/checkout/{session_id}", include_in_schema=False)
async def hosted_checkout_page(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Public payment page for a hosted checkout session. No auth — the
    session id is the capability. Renders one of three states: open form,
    completed message, or expired message."""
    # get_open_session raises CheckoutSessionNotFoundError → global handler
    # renders the structured 404 JSON. That's fine for a browser visitor —
    # they'll see the JSON, which is honest about what went wrong.
    cs = await cs_service.get_open_session(db, session_id)

    if cs.status == CheckoutSessionStatus.COMPLETED:
        tmpl = (_STATIC_DIR / "checkout_message.html").read_text(encoding="utf-8")
        html = _render_message_page(
            tmpl,
            title="Payment complete — 6ix Gateway",
            heading="Payment received",
            body="Thanks — this session has been paid.",
            icon_glyph="✓",
            icon_class="success",
            action_html=_render_action_link(cs.success_url),
        )
        return HTMLResponse(html, status_code=200)

    if cs.status == CheckoutSessionStatus.EXPIRED:
        tmpl = (_STATIC_DIR / "checkout_message.html").read_text(encoding="utf-8")
        html = _render_message_page(
            tmpl,
            title="Link expired — 6ix Gateway",
            heading="This checkout link has expired",
            body="Please contact the seller for a new link.",
            icon_glyph="⌛",
            icon_class="",
        )
        return HTMLResponse(html, status_code=410)  # 410 Gone — the resource used to exist

    # status == OPEN — fetch the client_secret off the linked PaymentIntent.
    if cs.payment_intent_id is None:
        # Defensive: session without a PI can't be paid — treat as expired.
        tmpl = (_STATIC_DIR / "checkout_message.html").read_text(encoding="utf-8")
        html = _render_message_page(
            tmpl,
            title="Unavailable — 6ix Gateway",
            heading="This checkout link is not available",
            body="Please contact the seller.",
            icon_glyph="!",
            icon_class="",
        )
        return HTMLResponse(html, status_code=410)

    intent = await db.get(PaymentIntent, cs.payment_intent_id)
    if intent is None or not intent.client_secret:
        tmpl = (_STATIC_DIR / "checkout_message.html").read_text(encoding="utf-8")
        html = _render_message_page(
            tmpl,
            title="Unavailable — 6ix Gateway",
            heading="This checkout link is not available",
            body="Please contact the seller.",
            icon_glyph="!",
            icon_class="",
        )
        return HTMLResponse(html, status_code=410)

    tmpl = (_STATIC_DIR / "checkout_session.html").read_text(encoding="utf-8")
    html = _render_open_page(
        tmpl,
        pk=settings.stripe_publishable_key,
        client_secret=intent.client_secret,
        amount_cents=cs.amount,
        currency=cs.currency,
        description=cs.description,
        success_url=cs.success_url,
    )
    return HTMLResponse(html, status_code=200)
