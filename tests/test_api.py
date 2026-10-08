"""M0 T0.2: skeleton, GET /v1/status, error envelope + codes."""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from apps.api.errors import ErrorCode, install_error_handlers
from apps.api.main import create_app
from engine.config.defaults import ParamError, ParamErrorCode


def _client_with(extra_routes: bool = False) -> TestClient:
    app: FastAPI = create_app()
    if extra_routes:

        @app.get("/test/number")
        async def number(n: int) -> dict[str, int]:
            return {"n": n}

        @app.get("/test/params")
        async def params() -> dict[str, str]:
            resolve: dict[str, str] = {}
            from engine.config.defaults import load_defaults, resolve_params

            resolve_params(load_defaults(), "detectors.rvol_climax_v5", {"not_real": 1})
            return resolve

    return TestClient(app)


def test_status_shape_and_values() -> None:
    client = _client_with()
    response = client.get("/v1/status")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["service"] == "cryptosos-api"
    assert body["api_version"] == "v1"
    assert body["status"] in {"ok", "degraded"}
    assert body["database"] in {"up", "down"}
    assert body["database"] == ("up" if body["status"] == "ok" else "down")
    assert body["engine_version"] and body["code_version"]
    assert body["dataset_schema_version"] and body["spec"]


def test_code_version_resolves_the_git_head() -> None:
    from engine.versions import REPO_ROOT, code_version

    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assert re.fullmatch(r"[0-9a-f]{7,40}", code_version())


def test_defaults_endpoint_lists_documented_params() -> None:
    client = _client_with()
    response = client.get("/v1/config/defaults")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["source"] == "config/defaults.yaml"
    detector: dict[str, Any] = body["defaults"]["detectors"]["rvol_climax_v5"]
    assert detector["cooldown"] == 5


def test_unknown_route_uses_the_error_envelope() -> None:
    client = _client_with()
    response = client.get("/v1/does-not-exist")
    assert response.status_code == 404
    body: dict[str, Any] = response.json()
    assert body["error"]["code"] == ErrorCode.NOT_FOUND
    assert isinstance(body["error"]["message"], str)
    assert isinstance(body["error"]["detail"], dict)


def test_request_validation_failure_is_422_validation_error() -> None:
    client = _client_with(extra_routes=True)
    response = client.get("/test/number", params={"n": "not-a-number"})
    assert response.status_code == 422
    body: dict[str, Any] = response.json()
    assert body["error"]["code"] == ErrorCode.VALIDATION_ERROR
    assert body["error"]["detail"]["errors"]


def test_undocumented_param_reaches_the_422_envelope() -> None:
    client = _client_with(extra_routes=True)
    response = client.get("/test/params")
    assert response.status_code == 422
    body: dict[str, Any] = response.json()
    assert body["error"]["code"] == ErrorCode.VALIDATION_ERROR
    assert body["error"]["detail"]["param"] == "not_real"
    assert body["error"]["detail"]["reason"] == ParamErrorCode.UNDOCUMENTED_PARAM


def test_cors_allows_the_documented_web_origin() -> None:
    client = _client_with()
    response = client.get("/v1/status", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_preflight_permits_the_queue_posts() -> None:
    client = _client_with()
    response = client.options(
        "/v1/data/ingest",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_cors_refuses_an_unlisted_origin() -> None:
    client = _client_with()
    response = client.get("/v1/status", headers={"Origin": "http://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_origins_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")
    client = _client_with()
    allowed = client.get("/v1/status", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    refused = client.get("/v1/status", headers={"Origin": "http://localhost:3000"})
    assert "access-control-allow-origin" not in refused.headers


def test_error_handlers_are_installed_on_a_bare_app() -> None:
    app = FastAPI()
    install_error_handlers(app)
    assert ParamError in app.exception_handlers
    assert RequestValidationError in app.exception_handlers
    assert Exception in app.exception_handlers
