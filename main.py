import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import dashboard, payments, refunds, webhooks
from app.core.config import settings
from app.core.errors import AppException, ErrorCode, ErrorResponse

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
