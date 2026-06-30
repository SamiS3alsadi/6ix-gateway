"""Structured error taxonomy.

Every error response renders as:
    {"error_code": "<MACHINE_CODE>", "message": "<human summary>", "detail": "<specifics>"}

Service code raises a typed `AppException` subclass; the global handler in
`main.py` serializes it. Routers do not need try/except blocks for these —
they propagate.
"""
from __future__ import annotations

from enum import Enum

from fastapi import status
from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    # --- Payment intents -----------------------------------------------------
    PAYMENT_NOT_FOUND = "PAYMENT_NOT_FOUND"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INVALID_STATE = "INVALID_STATE"
    INVALID_AMOUNT = "INVALID_AMOUNT"

    # --- Refunds -------------------------------------------------------------
    REFUND_NOT_ALLOWED = "REFUND_NOT_ALLOWED"
    REFUND_FAILED = "REFUND_FAILED"

    # --- Webhooks ------------------------------------------------------------
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    WEBHOOK_HANDLER_FAILED = "WEBHOOK_HANDLER_FAILED"

    # --- Auth / merchant API ------------------------------------------------
    UNAUTHORIZED = "UNAUTHORIZED"
    MERCHANT_NOT_FOUND = "MERCHANT_NOT_FOUND"
    API_KEY_NOT_FOUND = "API_KEY_NOT_FOUND"

    # --- Generic ------------------------------------------------------------
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorResponse(BaseModel):
    error_code: str = Field(..., description="Machine-readable code.")
    message: str = Field(..., description="Short human summary.")
    detail: str | None = Field(default=None, description="Specifics for debugging.")


class AppException(Exception):
    """Base for all domain errors. Subclasses set code/http_status/message."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_message: str = "internal error"

    def __init__(self, detail: str | None = None, message: str | None = None) -> None:
        self.detail = detail
        self.message = message or self.default_message
        super().__init__(detail or self.message)


# --- Payment intent errors --------------------------------------------------


class PaymentNotFoundError(AppException):
    code = ErrorCode.PAYMENT_NOT_FOUND
    http_status = status.HTTP_404_NOT_FOUND
    default_message = "payment intent not found"


class IdempotencyConflictError(AppException):
    code = ErrorCode.IDEMPOTENCY_CONFLICT
    http_status = status.HTTP_409_CONFLICT
    default_message = "idempotency key reused with different parameters"


class InvalidStateError(AppException):
    code = ErrorCode.INVALID_STATE
    http_status = status.HTTP_409_CONFLICT
    default_message = "operation not valid in current state"


class InvalidAmountError(AppException):
    code = ErrorCode.INVALID_AMOUNT
    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_message = "amount is outside the permitted range"


# --- Refund errors ----------------------------------------------------------


class RefundNotAllowedError(AppException):
    code = ErrorCode.REFUND_NOT_ALLOWED
    http_status = status.HTTP_409_CONFLICT
    default_message = "refund not permitted for this payment intent"


class RefundFailedError(AppException):
    code = ErrorCode.REFUND_FAILED
    http_status = status.HTTP_502_BAD_GATEWAY
    default_message = "refund could not be created"


# --- Webhook errors ---------------------------------------------------------


class InvalidSignatureError(AppException):
    code = ErrorCode.INVALID_SIGNATURE
    http_status = status.HTTP_400_BAD_REQUEST
    default_message = "stripe signature verification failed"


class InvalidPayloadError(AppException):
    code = ErrorCode.INVALID_PAYLOAD
    http_status = status.HTTP_400_BAD_REQUEST
    default_message = "webhook payload could not be parsed"


class WebhookHandlerFailedError(AppException):
    code = ErrorCode.WEBHOOK_HANDLER_FAILED
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_message = "webhook handler raised an exception"


# --- Auth / generic ---------------------------------------------------------


class UnauthorizedError(AppException):
    code = ErrorCode.UNAUTHORIZED
    http_status = status.HTTP_401_UNAUTHORIZED
    default_message = "missing or invalid credentials"


class MerchantNotFoundError(AppException):
    code = ErrorCode.MERCHANT_NOT_FOUND
    http_status = status.HTTP_404_NOT_FOUND
    default_message = "merchant not found"


class APIKeyNotFoundError(AppException):
    code = ErrorCode.API_KEY_NOT_FOUND
    http_status = status.HTTP_404_NOT_FOUND
    default_message = "api key not found"
