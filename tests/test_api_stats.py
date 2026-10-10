"""M5 (T5.2d) - ``POST /v1/stats`` + ``GET /v1/stats/{ref_id}`` (spec 8.2, 11).

Acceptance evidence produced here:
- happy path + provenance columns (dataset/engine/code/schema versions, seed,
  resolved_params) and the AC triple (grade + n_eff + CI + data_scope);
- reproducibility: two identical POSTs yield deep-equal ``result`` (ref_id /
  created_at differ); GET by ref_id returns the stored result verbatim;
- events persist idempotently (second identical POST adds no event rows);
- fail-closed: 422s (empty instrument_ids, unknown detector/version,
  SPEC_REQUIRED, undocumented params, unknown event_type/direction/timeframe,
  CUSTOM model, unknown model, bad scope, missing timeframe, instrument
  outside the scope universe), 404 unknown instrument / stats ref,
  501 perpetual;
- SCOPE_CLIPPED flag on DEVELOPMENT windows reaching past holdout_start;
- HOLDOUT gate: 422 without hypothesis_id, 404 unknown hypothesis,
  409 unopened, 200 once (grade null), second run 409, audit row written;
- ``resolve_stats_params`` pure unit tests (no database).

Every test seeds exactly one ingest window (the manifest picker serves the
latest manifest covering the instrument, so DEV and HOLDOUT windows are
seeded by different tests, never mixed).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text

from apps.api.main import create_app
from apps.api.settings import get_settings
from apps.api.stats import resolve_stats_params
from engine.config.defaults import load_defaults
from engine.data.bars import Timeframe
from engine.data.instruments import build_instrument, upsert_instruments
from engine.errors import ValidationError
from exchanges.base import Kline
from tests.support import BTC, DAY, T0, WIDE_BUDGET, FixtureAdapter, reset_research

ETH = build_instrument(
    venue="BINANCE",
    market_type="SPOT",
    asset="ETH",
    quote="USDT",
    native_symbol="ETHUSDT",
)
PERP = build_instrument(
    venue="BINANCE",
    market_type="PERPETUAL",
    asset="BTC",
    quote="USDT",
    native_symbol="BTCUSDT_PERP",
)
BYBIT = build_instrument(
    venue="BYBIT",
    market_type="SPOT",
    asset="BTC",
    quote="USDT",
    native_symbol="BTCUSDT",
)

# Small params so the fixture bars clear warmup (PD-11: overrides only;
# same values as tests/test_api_events.py SMALL_PARAMS).
SMALL_PARAMS: dict[str, Any] = {
    "vol_len": 3,
    "climax_mult": 3.0,
    "top_mult": 2.0,
    "range_len": 3,
    "trend_len": 2,
    "close_pos_lo": 0.30,
    "close_pos_hi": 0.70,
    "regime_len": 4,
    "bounce_len": 3,
    "bounce_atr": 1.5,
    "wick_min": 0.40,
    "cooldown": 0,
    "atr_len": 3,
    "fail_mode": "failed_bounce",
    "lookback_bars": 5,
}

DEV_START = T0  # 2024-01-01T00:00:00Z, inside IS
HOLDOUT_START = 1_778_371_200_000  # 2026-05-10T00:00:00Z, past the 50-day embargo
HOLDOUT_SPIKE_DAY = 8


def _bars(start_ms: int, count: int, spikes: set[int], base: float = 100.0) -> list[Kline]:
    rows: list[Kline] = []
    for i in range(count):
        open_ms = start_ms + i * DAY
        open_ = base + i
        if i in spikes:
            rows.append(
                Kline(
                    open_time=open_ms,
                    close_time=open_ms + DAY,
                    open=open_,
                    high=open_ + 1.0,
                    low=open_ - 12.0,
                    close=open_ - 11.0,
                    volume=10_000.0,
                    quote_volume=open_ * 10_000.0,
                    trades=5_000,
                )
            )
        else:
            rows.append(
                Kline(
                    open_time=open_ms,
                    close_time=open_ms + DAY,
                    open=open_,
                    high=open_ + 5.0,
                    low=open_ - 5.0,
                    close=open_ + 1.0,
                    volume=1_000.0,
                    quote_volume=open_ * 1_000.0,
                    trades=500,
                )
            )
    return rows


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "data"
    directory.mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(directory))
    return directory


@pytest.fixture()
def venue_dev(monkeypatch: pytest.MonkeyPatch) -> FixtureAdapter:
    adapter = FixtureAdapter(
        {
            ("BTCUSDT", Timeframe.D1): _bars(DEV_START, 60, {8, 28, 48}),
            ("ETHUSDT", Timeframe.D1): _bars(DEV_START, 60, {18, 38}, base=50.0),
        }
    )
    monkeypatch.setattr("apps.api.jobs.build_adapter", lambda params: adapter)
    return adapter


@pytest.fixture()
def venue_holdout(monkeypatch:pytest.MonkeyPatch) -> FixtureAdapter:
    adapter = FixtureAdapter(
        {("BTCUSDT", Timeframe.D1): _bars(HOLDOUT_START, 30, {HOLDOUT_SPIKE_DAY})}
    )
    monkeypatch.setattr("apps.api.jobs.build_adapter", lambda params: adapter)
    return adapter


@pytest.fixture()
def conn() -> Iterator[Connection]:
    engine = create_engine(get_settings().database_url, future=True)
    connection = engine.connect()
    for statement in (
        "DELETE FROM system.job",
        "DELETE FROM system.gap",
        "DELETE FROM system.data_quality",
        "DELETE FROM market.dataset_manifest",
        "DELETE FROM system.audit_log",
        "DELETE FROM market.instrument",
    ):
        connection.execute(text(statement))
    reset_research(connection)
    upsert_instruments(connection, [BTC, ETH, PERP, BYBIT])
    connection.commit()
    try:
        yield connection
    finally:
        connection.rollback()
        for statement in (
            "DELETE FROM system.job",
            "DELETE FROM system.gap",
            "DELETE FROM system.data_quality",
            "DELETE FROM market.dataset_manifest",
            "DELETE FROM system.audit_log",
            "DELETE FROM market.instrument",
            "TRUNCATE strategy.trade, strategy.backtest,"
            " research.event, research.event_outcome, research.stats_result,"
            " research.hypothesis, research.note, research.variant, research.experiment,"
            " research.experiment_family",
        ):
            connection.execute(text(statement))
        connection.commit()
        connection.close()
        engine.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _ingest(
    client: TestClient,
    *,
    instrument_ids: list[str],
    from_: str,
    to: str,
) -> str:
    response = client.post(
        "/v1/data/ingest",
        json={
            "venue": "BINANCE",
            "market_type": "SPOT",
            "instrument_ids": instrument_ids,
            "timeframes": ["1D"],
            "from": from_,
            "to": to,
            "storage_budget_gb": WIDE_BUDGET / 1024**3,
        },
    )
    assert response.status_code == 202, response.text
    view = client.get(f"/v1/jobs/{response.json()['job_id']}").json()
    assert view["status"] == "succeeded", view.get("error")
    return str(view["result_ref"])


@pytest.fixture()
def seeded_dev(client: TestClient, conn: Connection, venue_dev: FixtureAdapter, data_dir: Path) -> str:
    return _ingest(
        client,
        instrument_ids=[str(BTC.instrument_id), str(ETH.instrument_id)],
        from_="2024-01-01T00:00:00Z",
        to="2024-03-01T00:00:00Z",
    )


@pytest.fixture()
def seeded_holdout(
    client: TestClient, conn: Connection, venue_holdout: FixtureAdapter, data_dir: Path
) -> str:
    return _ingest(
        client,
        instrument_ids=[str(BTC.instrument_id)],
        from_="2026-05-10T00:00:00Z",
        to="2026-06-09T00:00:00Z",
    )


def _body(**over: Any) -> dict[str, Any]:
    req: dict[str, Any] = {
        "filter": {
            "detector": "RVOL_CLIMAX",
            "timeframe": "1D",
            "params": json.dumps(SMALL_PARAMS),
        },
        "instrument_ids": [str(BTC.instrument_id), str(ETH.instrument_id)],
    }
    for key, value in over.items():
        if key == "filter":
            req["filter"] = {**req["filter"], **value}
        else:
            req[key] = value
    return req


def _event_count(conn: Connection) -> int:
    return int(conn.execute(text("SELECT COUNT(*) FROM research.event")).scalar_one())


# --------------------------------------------------------------------------
# Happy path + provenance
# --------------------------------------------------------------------------


def test_post_stats_happy_path(client: TestClient, conn: Connection, seeded_dev: str) -> None:
    response = client.post("/v1/stats", json=_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {
        "ref_id",
        "filter",
        "data_scope",
        "boundaries",
        "dataset_version",
        "dataset_schema_version",
        "engine_version",
        "code_version",
        "resolved_params",
        "seed",
        "result",
        "grade",
        "n_eff",
        "model_sensitive",
        "scope",
        "created_at",
    }
    assert body["data_scope"] == "DEVELOPMENT"
    assert body["dataset_version"] == seeded_dev
    assert body["seed"] == 1
    assert body["boundaries"] == {
        "oos_start": "2025-07-20T00:00:00Z",
        "holdout_start": "2026-03-19T00:00:00Z",
    }
    stats = body["resolved_params"]["stats"]
    assert stats["embargo_bars"] == 50
    assert stats["market_event_k"] == 3
    assert stats["cluster_models"] == ["SYMBOL_GAP", "CALENDAR_WEEK"]
    assert stats["bootstrap_seed"] == 1
    result = body["result"]
    assert result["n_raw"] >= 3
    assert result["data_scope"] == "DEVELOPMENT"
    assert [e["h"] for e in result["horizons"]] == [1, 2, 3, 5, 10, 15, 20, 30, 40, 50]
    assert set(result["cluster_models"]) == {"SYMBOL_GAP", "CALENDAR_WEEK"}
    # Every fixture event sits in IS: the grade is D with the full AC triple present.
    assert result["grade"] == "D"
    assert body["grade"] == "D"
    assert result["n_eff"] == body["n_eff"]
    assert result["model_sensitive"] is False
    assert set(result["per_symbol"]) == {str(BTC.instrument_id), str(ETH.instrument_id)}
    assert sum(result["per_symbol"].values()) == result["n_raw"]
    assert body["scope"]["clipped"] is False
    assert body["scope"]["flag"] is None
    # Every ad-hoc run is recorded: a singleton family + one variant (8.4).
    block = result["multiple_testing"]
    assert block["variant_count"] == 1
    assert block["selection_method"] == "PRE_SPECIFIED"
    assert block["family_id"] is None  # request carried none; the row is the audit trail
    recorded = conn.execute(
        text(
            "SELECT f.variant_count AS variant_count FROM research.variant v"
            " JOIN research.experiment_family f ON f.family_id = v.family_id"
            " WHERE v.result_ref = :ref"
        ),
        {"ref": body["ref_id"]},
    ).first()
    assert recorded is not None, "the run must be recorded as a variant"
    assert recorded.variant_count == 1


def test_post_stats_reproducible(client: TestClient, conn: Connection, seeded_dev: str) -> None:
    first = client.post("/v1/stats", json=_body()).json()
    second = client.post("/v1/stats", json=_body()).json()
    assert first["ref_id"] != second["ref_id"]
    assert first["result"] == second["result"]


def test_events_persisted_idempotently(
    client: TestClient, conn: Connection, seeded_dev: str
) -> None:
    client.post("/v1/stats", json=_body())
    with conn.begin_nested():
        before = _event_count(conn)
    assert before >= 3
    client.post("/v1/stats", json=_body())
    with conn.begin_nested():
        assert _event_count(conn) == before


def test_get_stats_verbatim(client: TestClient, conn: Connection, seeded_dev: str) -> None:
    posted = client.post("/v1/stats", json=_body()).json()
    fetched = client.get(f"/v1/stats/{posted['ref_id']}").json()
    assert fetched["result"] == posted["result"]
    assert fetched["ref_id"] == posted["ref_id"]
    assert fetched["scope"] == posted["scope"]
    missing = client.get(f"/v1/stats/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


# --------------------------------------------------------------------------
# Fail-closed
# --------------------------------------------------------------------------


def test_post_stats_422s(client: TestClient, conn: Connection, seeded_dev: str) -> None:
    cases = [
        ("empty instrument_ids", _body(instrument_ids=[])),
        ("unknown detector", _body(filter={"detector": "NOPE"})),
        ("unknown version", _body(filter={"version": "9.9"})),
        ("spec-required detector", _body(filter={"detector": "TOPFAIL"})),
        ("undocumented param", _body(filter={"params": json.dumps({**SMALL_PARAMS, "nope": 1})})),
        ("unknown event_type", _body(filter={"event_type": "BOGUS"})),
        ("unknown direction", _body(filter={"direction": "SIDEWAYS"})),
        ("unknown timeframe", _body(filter={"timeframe": "7D"})),
        ("custom model", _body(cluster_models=["CUSTOM"])),
        ("unknown model", _body(cluster_models=["NOPE"])),
        ("bad scope", _body(data_scope="SOMETHING")),
        ("missing timeframe", _body(filter={"timeframe": None})),
    ]
    for name, payload in cases:
        if payload["filter"].get("timeframe") is None:
            del payload["filter"]["timeframe"]
        response = client.post("/v1/stats", json=payload)
        assert response.status_code == 422, name
        assert response.json()["error"]["code"] == "VALIDATION_ERROR", name


def test_post_stats_404_and_501(client: TestClient, conn: Connection, seeded_dev: str) -> None:
    missing = client.post("/v1/stats", json=_body(instrument_ids=[str(uuid.uuid4())]))
    assert missing.status_code == 404
    perp = client.post("/v1/stats", json=_body(instrument_ids=[str(PERP.instrument_id)]))
    assert perp.status_code == 501


def test_post_stats_rejects_outside_universe(
    client: TestClient, conn: Connection, seeded_dev: str
) -> None:
    response = client.post("/v1/stats", json=_body(instrument_ids=[str(BYBIT.instrument_id)]))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_post_stats_scope_clipped(client: TestClient, conn: Connection, seeded_dev: str) -> None:
    response = client.post("/v1/stats", json=_body(filter={"to": "2026-06-01T00:00:00Z"}))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"]["clipped"] is True
    assert body["scope"]["flag"] == "SCOPE_CLIPPED"
    assert body["scope"]["applied_to"] == "2026-03-19T00:00:00Z"
    assert body["scope"]["requested_to"] == "2026-06-01T00:00:00Z"


def test_post_stats_market_event_model(client: TestClient, conn: Connection, seeded_dev: str) -> None:
    response = client.post("/v1/stats", json=_body(cluster_models=["MARKET_EVENT"]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["result"]["cluster_models"]) == {"MARKET_EVENT"}
    assert body["resolved_params"]["stats"]["market_event_k"] == 3


# --------------------------------------------------------------------------
# HOLDOUT gate (spec 8.1 line 373, design section 6.3)
# --------------------------------------------------------------------------


def _open_holdout(conn: Connection, hyp_id: uuid.UUID) -> None:
    conn.execute(
        text("UPDATE research.hypothesis SET holdout_opened_at = now() WHERE hyp_id = :id"),
        {"id": hyp_id},
    )
    conn.commit()


def test_holdout_gate(client: TestClient, conn: Connection, seeded_holdout: str) -> None:
    holdout_body = _body(
        instrument_ids=[str(BTC.instrument_id)],
        data_scope="HOLDOUT",
    )
    no_hyp = client.post("/v1/stats", json=holdout_body)
    assert no_hyp.status_code == 422

    unknown = client.post(
        "/v1/stats", json={**holdout_body, "hypothesis_id": str(uuid.uuid4())}
    )
    assert unknown.status_code == 404

    hyp_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO research.hypothesis (hyp_id, title, statement, status,"
            " criteria, preregistered_at) VALUES (:id, 't', 's', 'TESTING',"
            " '{}', now())"
        ),
        {"id": hyp_id},
    )
    conn.commit()
    unopened = client.post("/v1/stats", json={**holdout_body, "hypothesis_id": str(hyp_id)})
    assert unopened.status_code == 409
    assert unopened.json()["error"]["code"] == "HOLDOUT_LOCKED"

    _open_holdout(conn, hyp_id)
    first = client.post("/v1/stats", json={**holdout_body, "hypothesis_id": str(hyp_id)})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["data_scope"] == "HOLDOUT"
    assert body["grade"] is None
    assert body["result"]["grade"] is None
    assert body["result"]["grade_reason"] == (
        "n/a — HOLDOUT scope is judged by pre-registered criteria"
    )
    assert body["result"]["n_raw"] >= 1
    stored_result = conn.execute(
        text("SELECT holdout_result FROM research.hypothesis WHERE hyp_id = :id"),
        {"id": hyp_id},
    ).scalar_one()
    assert stored_result["ref_id"] == body["ref_id"]
    audit = conn.execute(
        text(
            "SELECT COUNT(*) FROM system.audit_log WHERE action = 'stats.holdout_run'"
            " AND target = :id"
        ),
        {"id": str(hyp_id)},
    ).scalar_one()
    assert audit == 1

    second = client.post("/v1/stats", json={**holdout_body, "hypothesis_id": str(hyp_id)})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "HOLDOUT_LOCKED"


# --------------------------------------------------------------------------
# resolve_stats_params: pure unit tests (no database)
# --------------------------------------------------------------------------


def test_resolve_stats_params_defaults() -> None:
    params = resolve_stats_params(
        detector_id="RVOL_CLIMAX",
        cluster_models=None,
        bootstrap_seed=None,
        n_instruments=2,
        defaults=load_defaults(),
    )
    assert params["cluster_models"] == ["SYMBOL_GAP", "CALENDAR_WEEK"]
    assert params["bootstrap_seed"] == 1
    assert params["bootstrap_resamples"] == 5000
    assert params["horizons"] == [1, 2, 3, 5, 10, 15, 20, 30, 40, 50]
    assert params["embargo_bars"] == 50
    assert params["market_event_k"] == 3


def test_resolve_stats_params_overrides_and_k() -> None:
    params = resolve_stats_params(
        detector_id="RVOL_CLIMAX",
        cluster_models=["MARKET_EVENT"],
        bootstrap_seed=7,
        n_instruments=100,
        defaults=load_defaults(),
    )
    assert params["cluster_models"] == ["MARKET_EVENT"]
    assert params["bootstrap_seed"] == 7
    assert params["market_event_k"] == 10
    assert (
        resolve_stats_params(
            detector_id="RVOL_CLIMAX",
            cluster_models=None,
            bootstrap_seed=None,
            n_instruments=35,
            defaults=load_defaults(),
        )["market_event_k"]
        == 4
    )


def test_resolve_stats_params_rejects() -> None:
    with pytest.raises(ValidationError):
        resolve_stats_params(
            detector_id="RVOL_CLIMAX",
            cluster_models=["CUSTOM"],
            bootstrap_seed=None,
            n_instruments=2,
            defaults=load_defaults(),
        )
    with pytest.raises(ValidationError):
        resolve_stats_params(
            detector_id="RVOL_CLIMAX",
            cluster_models=["NOPE"],
            bootstrap_seed=None,
            n_instruments=2,
            defaults=load_defaults(),
        )
    with pytest.raises(ValidationError):
        resolve_stats_params(
            detector_id="UNKNOWN_DETECTOR",
            cluster_models=None,
            bootstrap_seed=None,
            n_instruments=2,
            defaults=load_defaults(),
        )
    with pytest.raises(ValidationError):
        resolve_stats_params(
            detector_id="RVOL_CLIMAX",
            cluster_models=None,
            bootstrap_seed=None,
            n_instruments=0,
            defaults=load_defaults(),
        )
