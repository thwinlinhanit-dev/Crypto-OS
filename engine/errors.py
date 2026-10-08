"""Engine-side error types.

PD-10 fail-closed needs errors that carry a spec error-envelope code out of the
engine without the engine importing the transport layer. ``apps.api.errors``
maps each class to its envelope code and HTTP status.
"""

from __future__ import annotations

from typing import Any, ClassVar


class EngineError(Exception):
    """Base class carrying the spec error-envelope code (section 11)."""

    code: ClassVar[str] = "INTERNAL_ERROR"
    http_status: ClassVar[int] = 500

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = detail or {}


class DataUnavailableError(EngineError):
    """The requested (venue, market_type, instrument, timeframe, range) has no data."""

    code = "DATA_UNAVAILABLE"
    http_status = 400


class NotImplementedMarketError(EngineError):
    """Market type exists for identity but is not served yet (perpetuals before M10)."""

    code = "NOT_IMPLEMENTED"
    http_status = 501


class ImmutableResultError(EngineError):
    """Refused write against content that already exists (PD-7, section 4.4)."""

    code = "IMMUTABLE_RESULT"
    http_status = 409


class DataIncompleteError(EngineError):
    """Data exists but the requested window is not fully covered."""

    code = "DATA_INCOMPLETE"
    http_status = 400


class ValidationError(EngineError):
    """The request or its resolved parameters are not servable as given (422)."""

    code = "VALIDATION_ERROR"
    http_status = 422


class NotFoundError(EngineError):
    """The named entity (dataset, gap, job, instrument) does not exist (404)."""

    code = "NOT_FOUND"
    http_status = 404
