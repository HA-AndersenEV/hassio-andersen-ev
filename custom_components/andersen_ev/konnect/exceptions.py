"""Typed exceptions for the Andersen EV konnect layer."""


class AndersenError(Exception):
    """Base exception for Andersen EV integration errors."""


class AndersenAuthError(AndersenError):
    """Raised when authentication fails (bad credentials, unknown email)."""


class AndersenConnectionError(AndersenError):
    """Raised when the API is unreachable or returns an unexpected HTTP status."""


class AndersenApiError(AndersenError):
    """Raised when the API response shape is unexpected."""


class AndersenSubscriptionError(AndersenError):
    """Raised when the live status subscription is refused or drops."""
