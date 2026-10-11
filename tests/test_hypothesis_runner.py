"""HF Phase 3 — the experiment runner tests (spec §3/§10).

The runner composes the M5 event→outcome→stats pipeline and evaluates the
hypothesis against its declared falsification criteria. These tests drive a
real ``compute_stats`` run (the M5 engine - never a duplicate) with a
seeded synthetic window inside the frozen scope, and assert:

- a stored stats_result row is produced with the reproducibility metadata;
- the evaluation and the decided transition follow the explicit rules;
- an empty event set fails closed (no partial row, PD-13).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, create_engine, text

from apps.api.settings import get_settings
from engine.errors import ValidationError
from engine.events.base import Event
from engine.events.outcomes import HorizonOutcome, Outcome
from engine.hypothesis import registry as hf_registry
from engine.hypothesis.runner import experiment_config_for, origin_ms, run_experiment

DAY_MS = 86_400_000
S_START = 1_704_067_200_000  # 2024-01-01T00:00:00Z
UNIVERSE = ("BINANCE:BTCUSDT", "BINANCE:ETHUSDT")


def _spec(**over: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "null_hypothesis": "OI contraction does not improve reclaim expectancy.",
        "alternative_hypothesis": "OI contraction improves reclaim expectancy.",
        "independent_vars": ["capitulation_indicator", "oi_change_pct"],
        "dependent_vars": ["forward_return_5"],
        "features": ["rvol", "oi_change_pct"],
        "population": "BTC, ETH daily 2024-2026",
        "timeframe": "1D",
        "observation_ts_rule": "bar close_time of the reclaim bar",
        "outcome_horizon": "5 bars",
        "expected_direction": "HIGHER",
        "data_requirements": ["market.open_interest"],
        "proposed_statistical_method": "block-bootstrap mean difference",
        "falsification_criteria": ["effect vanishes after fees"],
        "economic_significance_threshold": 0.2,
    }
    spec.update(over)
    return spec


def _events(n: int = 60) -> list[Event]:
    """Events inside the IS segment (before OOS_START) so the embargo/purge
    gate keeps them (PD-2: event_time is the bar CLOSE)."""
    events = []
    for i in range(n):
        open_ms = S_START + i * DAY_MS
        close_ms = open_ms + DAY_MS
        events.append(
            Event(
                detector_id="RVOL_CLIMAX",
                event_type="CAPIT",
                version="1",
                params_hash="h",
                instrument_id=UNIVERSE[i % 2],
                timeframe="1D",
                event_time=datetime.fromtimestamp(close_ms / 1000, tz=UTC),
                direction="UP",
                ref_price=100.0,
                invalidation_price=99.0,
                regime={},
                features={},
                trigger={},
                lookback_gap=False,
                dataset_version="dv",
                engine_version="0.1.0",
                event_index=i,
            )
        )
    return events


def _outcomes(events: list[Event], *, mean: float) -> dict[str, Outcome]:
    outcomes = {}
    for i, event in enumerate(events):
        sign = 1.0 if i % 2 == 0 else -0.5  # a mixed but mostly positive set
        outcomes[event.event_id] = Outcome(
            event_id=event.event_id,
            event_index=event.event_index,
            direction=event.direction,
            ref_price=event.ref_price,
            invalidation_price=event.invalidation_price,
            r_basis="INVALIDATION",
            r_value=1.0,
            horizons={
                h: HorizonOutcome(
                    horizon=h,
                    complete=True,
                    fwd_ret_pct=mean + sign,
                    fwd_ret_r=mean + sign,
                )
                for h in (1, 3, 5, 10, 20, 40)
            },
        )
    return outcomes


@pytest.fixture()
def conn() -> Iterator[Connection]:
    engine = create_engine(
        get_settings().database_url, future=True, connect_args={"connect_timeout": 3}
    )
    try:
        connection = engine.connect()
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"database unreachable: {exc}")
    try:
        yield connection
    finally:
        connection.close()
        engine.dispose()


#: Append-only tables are emptied with TRUNCATE (their row triggers reject
#: DELETE outright, PD-13); the FK graph truncates together.
_RESET_SQL = (
    "TRUNCATE research.stats_result, research.research_ledger,"
    " research.hypothesis_relationship, research.hypothesis_version,"
    " research.hypothesis CASCADE"
)


@pytest.fixture(autouse=True)
def clean(conn: Connection) -> Iterator[None]:
    conn.execute(text("DELETE FROM system.audit_log"))
    conn.execute(text(_RESET_SQL))
    conn.commit()
    try:
        yield
    finally:
        conn.execute(text("DELETE FROM system.audit_log"))
        conn.execute(text(_RESET_SQL))
        conn.commit()


def _seed(conn: Connection, **over: Any) -> str:
    hyp_id = hf_registry.create_hypothesis(
        conn,
        title="OI contraction improves reclaim expectancy",
        domain="CAPITULATION_RECOVERY",
        origin="HUMAN",
        spec=_spec(**over),
    )
    conn.commit()
    return hyp_id


def test_the_runner_composes_the_m5_pipeline_and_evaluates(conn: Connection) -> None:
    """The spec §3 core: the run produces a stored result + an evaluation +
    a decided transition - through the M5 engine, never a duplicate."""
    hyp_id = _seed(conn)
    config = experiment_config_for(conn, hypothesis_id=hyp_id)
    assert config.scope_family == "binance_spot_usdt_default"
    events = _events()
    result = run_experiment(
        conn,
        hypothesis_id=hyp_id,
        config=config,
        events=events,
        outcomes=_outcomes(events, mean=0.3),
        n_raw=len(events),
        bar_ms=DAY_MS,
    )
    conn.commit()

    assert result["config_hash"] == config.config_hash()
    # the stored row carries the reproducibility metadata (spec §1.2.3)
    row = conn.execute(
        text(
            "SELECT ref_id, engine_version, code_version, resolved_params, seed"
            " FROM research.stats_result WHERE ref_id = :ref"
        ),
        {"ref": result["ref_id"]},
    ).first()
    assert row is not None
    assert row.engine_version == "0.1.0"
    assert row.seed == config.bootstrap_seed
    # the config_hash rides in resolved_params (the reproducibility anchor)
    import json as _json_mod

    assert config.config_hash() in _json_mod.dumps(row.resolved_params)
    # the evaluation and the decided transition are present and explicit
    assert result["evaluation"]["falsified"] in (True, False)
    assert result["transition"] in (
        "EXPLORATORY", "FAILED_STATISTICAL_TEST", "FAILED_ROBUSTNESS",
        "INVALID_EXPERIMENT",
    )


def test_the_runner_evaluates_against_the_declared_criteria(conn: Connection) -> None:
    """The evaluation applies the hypothesis's DECLARED falsification
    criteria (spec §13) - a low mean below the economic threshold falsifies."""
    hyp_id = _seed(conn, economic_significance_threshold=5.0)
    config = experiment_config_for(conn, hypothesis_id=hyp_id)
    events = _events()
    result = run_experiment(
        conn,
        hypothesis_id=hyp_id,
        config=config,
        events=events,
        outcomes=_outcomes(events, mean=0.01),
        n_raw=len(events),
        bar_ms=DAY_MS,
    )
    conn.commit()
    # the criterion fired: the reject_reason carries the fired criterion's
    # DETAIL (the horizon + the below-threshold mean), not its name
    assert result["evaluation"]["falsified"] is True
    assert "below economic threshold" in str(result["evaluation"]["reject_reason"])
    fired = [c for c in result["evaluation"]["criteria"] if c["fired"]]
    assert fired and fired[0]["criterion"] == "effect vanishes after fees"
    assert result["transition"] == "FAILED_STATISTICAL_TEST"


def test_an_empty_event_set_fails_closed(conn: Connection) -> None:
    """No events -> no partial row (PD-13): the caller's transaction rolls
    back and the hypothesis is untouched."""
    hyp_id = _seed(conn)
    config = experiment_config_for(conn, hypothesis_id=hyp_id)
    with pytest.raises(ValidationError):
        run_experiment(
            conn,
            hypothesis_id=hyp_id,
            config=config,
            events=[],
            outcomes={},
            n_raw=0,
            bar_ms=DAY_MS,
        )
    conn.rollback()
    count = conn.execute(
        text("SELECT count(*) FROM research.stats_result")
    ).scalar()
    assert count == 0  # no partial row survived the failed run (PD-13)


def test_the_origin_ms_uses_the_bar_open(conn: Connection) -> None:
    """PD-2: the origin is the bar OPEN; event_time is the bar CLOSE."""
    event = _events(1)[0]
    origin = origin_ms(event, DAY_MS)
    assert origin == S_START  # close_ms (S_START+DAY_MS) - bar_ms


def test_the_config_hash_is_stable_across_runs(conn: Connection) -> None:
    """The reproducibility contract: the same hypothesis version + scope +
    seed always hashes identically (spec §1.2.3)."""
    hyp_id = _seed(conn)
    a = experiment_config_for(conn, hypothesis_id=hyp_id)
    b = experiment_config_for(conn, hypothesis_id=hyp_id)
    assert a.config_hash() == b.config_hash()


def test_a_holdout_run_is_a_separate_config(conn: Connection) -> None:
    """The holdout is a separate experiment (spec §14.1), never an implicit
    widening: its config hash differs from the development run's."""
    hyp_id = _seed(conn)
    dev = experiment_config_for(conn, hypothesis_id=hyp_id, data_scope="DEVELOPMENT")
    holdout = experiment_config_for(conn, hypothesis_id=hyp_id, data_scope="HOLDOUT")
    assert dev.config_hash() != holdout.config_hash()
    assert holdout.data_scope == "HOLDOUT"
