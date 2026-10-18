"""Application exceptions and their HTTP mapping."""

from __future__ import annotations

from fastapi import status


class AppError(Exception):
    """Base for errors that map cleanly onto an HTTP response."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"


class PermissionError_(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class ProviderError(AppError):
    """An upstream model provider failed."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "provider_error"


class NoProviderAvailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "no_provider"


class BudgetExceededError(AppError):
    """A workflow hit its cost, token, task or time ceiling."""

    status_code = status.HTTP_402_PAYMENT_REQUIRED
    code = "budget_exceeded"


class QueueUnavailableError(AppError):
    """Work could not be handed to the background workers."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "queue_unavailable"
