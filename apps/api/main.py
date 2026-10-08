"""FastAPI application entrypoint (M0 T0.2, extended by M1).

Scope: the skeleton, GET /v1/status, the error envelope and OpenAPI for client
generation, plus the M1 data surface (design note section 10). Research/
market endpoints stay undeclared until their milestone (PD-9).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apps.api.data import router as data_router
from apps.api.db import database_reachable
from apps.api.errors import install_error_handlers
from apps.api.settings import get_settings
from engine.versions import (
    DATASET_SCHEMA_VERSION,
    ENGINE_VERSION,
    code_version,
    service_version,
)

API_VERSION = "v1"


class StatusResponse(BaseModel):
    """GET /v1/status body (spec section 11, 'System')."""

    service: str
    status: Literal["ok", "degraded"]
    api_version: str
    service_version: str
    engine_version: str
    code_version: str
    dataset_schema_version: str
    database: Literal["up", "down"]
    spec: str


class DefaultsResponse(BaseModel):
    """GET /v1/config/defaults body: where parameters resolve from (PD-11)."""

    source: str
    defaults: dict[str, Any]


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CryptoOS API",
        version=service_version(),
        description=(
            "Read-only research API. No order-execution surface exists in v1 (PD-1). "
            "Errors use the envelope {error:{code,message,detail}}."
        ),
    )
    install_error_handlers(app)
    # The web app (3000) calls this API (8000) from the browser, so the two
    # origins differ; without this every UI fetch is blocked before it leaves
    # the browser. Only the methods this read-only surface exposes (PD-1).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
        max_age=600,
    )
    # M1 data surface (spec section 11, design note section 10). Reads plus
    # queue-only POSTs: no endpoint ever places an order (PD-1).
    app.include_router(data_router)

    @app.get(f"/{API_VERSION}/status", response_model=StatusResponse, tags=["system"])
    async def status() -> StatusResponse:
        db_up = database_reachable()
        return StatusResponse(
            service="cryptosos-api",
            status="ok" if db_up else "degraded",
            api_version=API_VERSION,
            service_version=service_version(),
            engine_version=ENGINE_VERSION,
            code_version=code_version(),
            dataset_schema_version=DATASET_SCHEMA_VERSION,
            database="up" if db_up else "down",
            spec="IMPLEMENTATION_SPEC_v1.1.1",
        )

    @app.get(
        "/v1/config/defaults", response_model=DefaultsResponse, tags=["system"]
    )
    async def config_defaults() -> DefaultsResponse:
        """Documented defaults (PD-11): the file the API resolves parameters against."""
        from engine.config.defaults import load_defaults

        return DefaultsResponse(source="config/defaults.yaml", defaults=load_defaults())

    return app


app = create_app()
