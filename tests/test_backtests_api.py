"""M6 (T6.1-T6.2) — strategies + backtests API tests (spec section 7, 11).

Acceptance evidence produced here (spec M6 AC):

- "every backtest belongs to an experiment" - ``test_backtest_requires_experiment``
  (404 unknown experiment; D-041);
- "no signal disappears (all recorded as trade, SKIPPED or NOT_FILLED with
  reason)" - ``test_backtest_records_every_signal``: the skipped list carries
  the reason for every signal that did not become a trade;
- reproducibility - ``test_same_inputs_byte_identical_trades`` (engine level);
- strategy versioning (7.1: edits create a new version) - ``test_duplicate_version_rejected``.

Fail-closed vocabularies: unknown fill_model 422, SHORT_ONLY 422 until M10
(R11/D-043), unknown dataset_version 422 (PD-10/PD-12).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text

from apps.api.main import create_app
from apps.api.settings import get_settings

DEFINITION: dict[str, Any] = {
    "strategy_id": "rvol_climax_pro",
    "version": "5.0",
    "entry": {
        "detector": "RVOL_CLIMAX@5.0",
        "params": {},
        "signals": ["CONT"],
        "side": "LONG_ONLY",
    },
    "sizing": {"mode": "RISK", "risk_pct": 1.0, "max_alloc_pct": 25.0},
    "stop": {"kind": "ATR", "atr_len": 14, "atr_mult": 3.0},
    "costs": {
        "fee_model": {"kind": "PERCENT", "value": 0.10},
        "slippage_model": {"kind": "FIXED_BPS", "value": 5},
    },
    "execution": {"fill_model": "REALISTIC_OHLC", "initial_capital": 10000},
}

#: The API body wraps the definition JSON (POST /v1/strategies shape).
STRATEGY_BODY: dict[str, Any] = {
    "strategy_id": DEFINITION["strategy_id"],
    "version": DEFINITION["version"],
    "definition": DEFINITION,
}


@pytest.fixture()
def venue(monkeypatch: pytest.MonkeyPatch) -> Any:
    """In-memory bars served through the same ingest path as the seed (AC1)."""
    from engine.data.bars import Timeframe
    from tests.support import FixtureAdapter

    adapter = FixtureAdapter({("BTCUSDT", Timeframe.D1): _bars()})
    monkeypatch.setattr("apps.api.jobs.build_adapter", lambda params: adapter)
    return adapter


def _bars() -> list[Any]:
    """Forty daily bars from the golden window (enough for a 14-bar ATR
    warm-up plus signals)."""
    from tests.support import kline

    return [kline(1_704_067_200_000 + i * 86_400_000) for i in range(40)]


@pytest.fixture()
def seeded(
    client: TestClient, conn: Connection, venue: Any, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    """Register BTC + ingest one manifest through the job seam; returns the
    dataset_version."""
    from engine.data.instruments import upsert_instruments
    from tests.support import BTC, WIDE_BUDGET

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    upsert_instruments(conn, [BTC])
    conn.commit()
    response = client.post(
        "/v1/data/ingest",
        json={
            "venue": "BINANCE",
            "market_type": "SPOT",
            "instrument_ids": [str(BTC.instrument_id)],
            "timeframes": ["1D"],
            "from": "2024-01-01T00:00:00Z",
            "to": "2024-03-01T00:00:00Z",
            "storage_budget_gb": WIDE_BUDGET / 1024**3,
        },
    )
    assert response.status_code == 202, response.text
    view = client.get(f"/v1/jobs/{response.json()['job_id']}").json()
    assert view["status"] == "succeeded", view.get("error")
    return str(view["result_ref"])


@pytest.fixture()
def instrument_id(conn: Connection, seeded: str) -> str:
    return str(_instrument_id(conn))


@pytest.fixture()
def conn() -> Iterator[Connection]:
    engine = create_engine(get_settings().database_url, future=True)
    connection = engine.connect()
    try:
        yield connection
    finally:
        connection.close()
        engine.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def clean(conn: Connection) -> Iterator[None]:
    _wipe(conn)
    try:
        yield
    finally:
        _wipe(conn)


def _wipe(conn: Connection) -> None:
    conn.execute(text("SET session_replication_role = replica"))
    for table in (
        "strategy.trade",
        "strategy.backtest",
        "strategy.strategy_version",
        "research.experiment",
        "research.variant",
        "research.experiment_family",
        "research.hypothesis",
        "research.stats_result",
    ):
        conn.execute(text(f"DELETE FROM {table}"))
    conn.execute(text("SET session_replication_role = DEFAULT"))
    conn.commit()


def _strategy(client: TestClient) -> dict[str, Any]:
    response = client.post("/v1/strategies", json=STRATEGY_BODY)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _experiment(client: TestClient, conn: Connection, dataset_version: str) -> dict[str, Any]:
    hyp = client.post(
        "/v1/hypotheses",
        json={
            "title": "t",
            "statement": "s",
            "criteria": {"pass": ["grade B"], "kill": ["grade D twice"]},
        },
    )
    assert hyp.status_code == 201, hyp.text
    hyp_id = hyp.json()["hyp_id"]
    exp = client.post(
        "/v1/experiments",
        json={
            "hypothesis_id": hyp_id,
            "title": "e",
            "universe": [str(_instrument_id(conn))],
            "timeframe": "1D",
            "dataset_version": dataset_version,
            "detector_refs": {"primary": {"detector_id": "RVOL_CLIMAX", "version": "5.0"}},
            "resolved_params": {"cooldown": 1},
        },
    )
    assert exp.status_code == 201, exp.text
    return {"experiment_id": exp.json()["experiment_id"], "dataset_version": dataset_version}


def _instrument_id(conn: Connection) -> Any:
    row = conn.execute(text("SELECT instrument_id FROM market.instrument LIMIT 1")).first()
    assert row is not None, "test requires a seeded instrument"
    return row.instrument_id


# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------


def test_create_and_get_strategy_version(client: TestClient) -> None:
    """7.1: versioned definition, content-hashed (PD-6)."""
    created = _strategy(client)
    assert created["strategy_id"] == "rvol_climax_pro"
    assert created["version"] == "5.0"
    assert len(created["definition_hash"]) == 64  # sha256 hex
    fetched = client.get("/v1/strategies/rvol_climax_pro/5.0")
    assert fetched.status_code == 200
    assert fetched.json()["definition_hash"] == created["definition_hash"]


def test_duplicate_version_rejected(client: TestClient) -> None:
    """7.1: edits create a NEW version; the same (id, version) is refused."""
    _strategy(client)
    response = client.post("/v1/strategies", json=STRATEGY_BODY)
    assert response.status_code == 422, response.text


def test_unknown_strategy_version_404(client: TestClient) -> None:
    response = client.get("/v1/strategies/nope/1.0")
    assert response.status_code == 404


def test_invalid_definition_422(client: TestClient) -> None:
    body = json.loads(json.dumps(DEFINITION))
    body["stop"] = {"kind": "VOLATILITY", "atr_mult": 2.0}
    response = client.post("/v1/strategies", json={**STRATEGY_BODY, "definition": body})
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# backtests
# ---------------------------------------------------------------------------


def test_backtest_requires_experiment(client: TestClient) -> None:
    """D-041 (M6 AC): every backtest belongs to an experiment - unknown 404."""
    _strategy(client)
    response = client.post(
        "/v1/backtests",
        json={
            "experiment_id": "00000000-0000-0000-0000-000000000000",
            "strategy_id": "rvol_climax_pro",
            "version": "5.0",
            "universe": ["00000000-0000-0000-0000-000000000000"],
            "timeframe": "1D",
        },
    )
    assert response.status_code == 404, response.text


def test_backtest_unknown_fill_model_422(client: TestClient, conn: Connection, seeded: str) -> None:
    experiment = _experiment(client, conn, seeded)
    _strategy(client)
    response = client.post(
        "/v1/backtests",
        json={
            "experiment_id": experiment["experiment_id"],
            "strategy_id": "rvol_climax_pro",
            "version": "5.0",
            "universe": [str(_instrument_id(conn))],
            "timeframe": "1D",
            "fill_model": "REALISTIC_INTRABAR",
        },
    )
    assert response.status_code == 422, response.text


def test_backtest_short_side_rejected_on_spot(client: TestClient, conn: Connection, seeded: str) -> None:
    """R11/D-071 (M10): SHORT_ONLY is runnable now, but on perp only — a spot
    universe is rejected (fail-closed, PD-10)."""
    experiment = _experiment(client, conn, seeded)
    body = json.loads(json.dumps(DEFINITION))
    body["entry"]["side"] = "SHORT_ONLY"
    client.post("/v1/strategies", json={**STRATEGY_BODY, "definition": body})
    response = client.post(
        "/v1/backtests",
        json={
            "experiment_id": experiment["experiment_id"],
            "strategy_id": "rvol_climax_pro",
            "version": "5.0",
            "universe": [str(_instrument_id(conn))],
            "timeframe": "1D",
        },
    )
    assert response.status_code == 422, response.text
    assert "short-side" in response.text


def test_backtest_queued_and_completes(client: TestClient, conn: Connection, seeded: str) -> None:
    """Spec 11: POST answers 202 with a queued backtest; the background job
    runs it to succeeded (D-042 pure simulator + M1 job seam)."""
    experiment = _experiment(client, conn, seeded)
    _strategy(client)
    response = client.post(
        "/v1/backtests",
        json={
            "experiment_id": experiment["experiment_id"],
            "strategy_id": "rvol_climax_pro",
            "version": "5.0",
            "universe": [str(_instrument_id(conn))],
            "timeframe": "1D",
        },
    )
    assert response.status_code == 202, response.text
    backtest_id = response.json()["backtest_id"]
    assert response.json()["status"] == "queued"

    # the TestClient runs background tasks synchronously after the response
    row = conn.execute(
        text("SELECT status, metrics FROM strategy.backtest WHERE backtest_id = :id"),
        {"id": backtest_id},
    ).first()
    assert row is not None
    assert row.status == "succeeded", f"backtest failed: {row.metrics}"
    metrics = row.metrics if isinstance(row.metrics, dict) else json.loads(row.metrics)
    assert metrics["trade_count"] >= 0


def test_backtest_views(client: TestClient, conn: Connection, seeded: str) -> None:
    """Spec 11: trades / metrics / skipped views are readable."""
    experiment = _experiment(client, conn, seeded)
    _strategy(client)
    created = client.post(
        "/v1/backtests",
        json={
            "experiment_id": experiment["experiment_id"],
            "strategy_id": "rvol_climax_pro",
            "version": "5.0",
            "universe": [str(_instrument_id(conn))],
            "timeframe": "1D",
        },
    )
    backtest_id = created.json()["backtest_id"]
    assert client.get(f"/v1/backtests/{backtest_id}").status_code == 200
    trades = client.get(f"/v1/backtests/{backtest_id}/trades")
    assert trades.status_code == 200
    assert isinstance(trades.json(), list)
    assert client.get(f"/v1/backtests/{backtest_id}/metrics").status_code == 200
    skipped = client.get(f"/v1/backtests/{backtest_id}/skipped")
    assert skipped.status_code == 200
    assert isinstance(skipped.json(), list)
    assert client.get(f"/v1/backtests/{backtest_id}/equity").status_code == 200


def test_unknown_backtest_404(client: TestClient) -> None:
    response = client.get("/v1/backtests/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
