from __future__ import annotations


class RealtimeDataError(RuntimeError):
    """Base error for realtime provider failures."""


class ProviderAuthError(RealtimeDataError):
    """Raised when realtime provider credentials are missing or invalid."""


class ProviderTimeoutError(RealtimeDataError):
    """Raised when realtime provider requests time out."""


class ProviderBadResponseError(RealtimeDataError):
    """Raised when realtime provider returns an unusable response."""
