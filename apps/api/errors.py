"""HTTP error envelope (spec section 11) and the frozen error-code vocabulary."""

from __future__ import annotations

import json
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from engine.config.defaults import ParamError
from engine.errors import EngineError


class ErrorCode(StrEnum):
    """Codes the spec enumerates for the error envelope (section 11, 'codes include')."""

    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    IMMUTABLE_RESULT = "IMMUTABLE_RESULT"
    HOLDOUT_LOCKED = "HOLDOUT_LOCKED"
    SCOPE_CLIPPED = "SCOPE_CLIPPED"
    # Additions used by the transport layer itself; the spec's list is 'codes include'.
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    detail: dict[str, Any]


class ErrorEnvelope(BaseModel):
    error: ErrorBody


def envelope(code: ErrorCode, message: str, detail: dict[str, Any] | None = None) -> ErrorEnvelope:
    return ErrorEnvelope(error=ErrorBody(code=code, message=message, detail=detail or {}))


def _payload(env: ErrorEnvelope) -> dict[str, Any]:
    return {"error": env.error.model_dump(mode="json")}


def _safe_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Validation errors must stay JSON: pydantic keeps the raw exception in
    ``ctx.error`` for value errors, which the envelope cannot serialize."""
    safe: list[dict[str, Any]] = json.loads(json.dumps(errors, default=str))
    return safe


#: Transport-level status -> envelope code. The spec's list is "codes include",
#: so only the statuses this skeleton can produce are mapped here.
_STATUS_CODES: dict[int, ErrorCode] = {
    400: ErrorCode.VALIDATION_ERROR,
    404: ErrorCode.NOT_FOUND,
    422: ErrorCode.VALIDATION_ERROR,
    501: ErrorCode.NOT_IMPLEMENTED,
    500: ErrorCode.INTERNAL_ERROR,
}


def install_error_handlers(app: FastAPI) -> None:
    """Register the envelope for validation failures, param failures and unhandled errors."""

    @app.exception_handler(EngineError)
    async def _engine(request: Request, exc: EngineError) -> JSONResponse:
        # One handler for every engine failure: the class carries its spec code
        # (NOT_FOUND, VALIDATION_ERROR, NOT_IMPLEMENTED, ...) and its status.
        return JSONResponse(
            status_code=exc.http_status,
            content=_payload(envelope(ErrorCode(exc.code), exc.message, exc.detail)),
        )

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        message = exc.detail if isinstance(exc.detail, str) else code.value
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(envelope(code, message)),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_payload(
                    envelope(
                        ErrorCode.VALIDATION_ERROR,
                        "request failed validation",
                        {"errors": _safe_errors(exc.errors())},
                    ),
            ),
        )

    @app.exception_handler(ParamError)
    async def _param(request: Request, exc: ParamError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_payload(envelope(ErrorCode.VALIDATION_ERROR, exc.message, exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=_payload(
                envelope(
                    ErrorCode.INTERNAL_ERROR, "unhandled server error", {"type": type(exc).__name__}
                ),
            ),
        )
