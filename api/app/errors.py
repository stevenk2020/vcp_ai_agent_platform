"""One error type for every API failure, rendered as {"error", "message"}."""

from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message
        self.headers = headers or {}


def unauthenticated() -> ApiError:
    return ApiError(401, "unauthenticated", "Sign in to continue.")


def forbidden() -> ApiError:
    return ApiError(403, "forbidden", "You are not allowed to do this.")


def invalid_code() -> ApiError:
    return ApiError(401, "invalid_or_expired_code", "The code is invalid or has expired.")


def not_found(what: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", what)


def conflict(message: str) -> ApiError:
    return ApiError(409, "conflict", message)


def last_admin() -> ApiError:
    return ApiError(409, "last_admin", "This would leave the platform without an active admin.")


def not_pending() -> ApiError:
    return ApiError(409, "not_pending", "There is no request to make: you are already approved.")


def already_handled() -> ApiError:
    return ApiError(409, "already_handled", "Someone else already handled this person. Refresh the list.")


def not_configured(message: str) -> ApiError:
    return ApiError(503, "not_configured", message)


def try_again_later() -> ApiError:
    return ApiError(503, "try_again_later", "Please try again later.")


def rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        429, "rate_limited", "Too many requests. Please try again later.",
        headers={"Retry-After": str(max(1, retry_after))},
    )
