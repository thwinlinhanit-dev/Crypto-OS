"""HF Phase 3 — the experiment runner (spec §3/§10; the Phase 3 second half).

The runner executes an HF experiment by composing the M5 event→outcome→
stats pipeline. It is the single place where a hypothesis version becomes
a stored ``research.stats_result`` row: the same path the API's ``/v1/stats``
uses (HF-001: never a duplicate engine). The HF adds only the composition:
resolve the hypothesis's config → run the M5 pipeline → evaluate against
the declared falsification criteria → decide the evidence-ladder transition.

Determinism (PD-6): the only RNG is the declared bootstrap seed; the run
has no clock of its own (all times come from the frozen scope). The runner
takes an already-open connection - the caller owns the transaction, so a
failed evaluation leaves no partial row (PD-13).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC
from typing import Any, Final
from uuid import UUID

from sqlalchemy import Connection, text

from engine.config.validation import load_evidence_grade
from engine.errors import ValidationError
from engine.events.base import Event
from engine.events.outcomes import Outcome
from engine.hypothesis.evaluate import (
    Evaluation,
    decide_transition,
)
from engine.hypothesis.evaluate import (
    evaluate as evaluate_hypothesis,
)
from engine.hypothesis.experiment import ExperimentConfig, build_experiment_config
from engine.hypothesis.registry import get_hypothesis
from engine.scopes import embargo_purge, load_boundaries
from engine.stats.compute import Observation, compute_stats
from engine.versions import DATASET_SCHEMA_VERSION, ENGINE_VERSION, code_version

_MS_PER_SECOND: Final[int] = 1000


def origin_ms(event: Event, bar_ms: int) -> int:
    """Bar open_time of the event: ``event_time`` is the bar CLOSE (spec 6.2)."""
    moment = event.event_time
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * _MS_PER_SECOND) - bar_ms


def run_experiment(
    conn: Connection,
    *,
    hypothesis_id: str,
    config: ExperimentConfig,
    events: Sequence[Event],
    outcomes: Mapping[str, Outcome],
    n_raw: int,
    bar_ms: int,
    family_id: str | None = None,
    variant_count: int = 1,
) -> dict[str, Any]:
    """Run the M5 stats pipeline for an HF experiment and evaluate the
    hypothesis against its declared falsification criteria (spec §3).

    ``events``/``outcomes`` are the detector-produced inputs (the caller
    loads them via the same ``_load_window``/detector path the API uses).
    The runner composes; it never re-derives the statistics (HF-001).

    Returns the stored result dict + the evaluation + the decided
    transition. The transaction is the caller's: on a validation failure
    nothing is written (PD-13).
    """
    if not events:
        raise ValidationError(
            "no events were produced for the experiment window",
            {"hypothesis_id": hypothesis_id},
        )
    if n_raw < 1:
        raise ValidationError(
            "n_raw must be at least 1 (the experiment covers no observations)",
            {"n_raw": n_raw},
        )

    origins = [origin_ms(e, bar_ms) for e in events]
    observations = [
        Observation(
            event_id=e.event_id,
            instrument_id=str(e.instrument_id),
            event_type=e.event_type,
            origin_ms=origin,
            horizons=dict(outcomes[e.event_id].horizons),
        )
        for e, origin in zip(events, origins, strict=True)
        if e.event_id in outcomes
    ]
    if not observations:
        raise ValidationError(
            "no observations survived the outcome computation",
            {"hypothesis_id": hypothesis_id},
        )

    # the embargo/purge gate (the M5 path the API's /v1/stats uses)
    report = embargo_purge(
        origins,
        boundaries=load_boundaries(config.scope_family),
        horizon_h=max(config.horizons),
        embargo_e=_embargo_bars(config),
        bar_ms=bar_ms,
    )
    kept = {assignment.origin_ms for assignment in report.kept}
    observations = [o for o in observations if o.origin_ms in kept]
    if not observations:
        raise ValidationError(
            "every observation was excluded by the embargo/purge gate",
            {"hypothesis_id": hypothesis_id},
        )

    hypothesis = get_hypothesis(conn, hypothesis_id)
    spec = hypothesis["spec"] or {}

    thresholds = load_evidence_grade()
    result = compute_stats(
        observations,
        boundaries=load_boundaries(config.scope_family),
        data_scope=config.data_scope,
        event_filter={
            "hypothesis_id": hypothesis_id,
            "version": config.version,
            "scope_family": config.scope_family,
            "method": config.method,
            "expected_direction": config.expected_direction,
        },
        cluster_models=list(config.cluster_models),
        seed=config.bootstrap_seed,
        params=config.stats_params(),
        grade_thresholds=thresholds,
        n_raw=n_raw,
        bar_ms=bar_ms,
        family_id=family_id,
        variant_count=variant_count,
        selection_method=config.selection_method,
    )

    ref_id = _persist_stats_result(
        conn,
        hypothesis_id=hypothesis_id,
        config=config,
        result=result,
        family_id=family_id,
    )

    evaluation = evaluate_hypothesis(
        hypothesis_id=hypothesis_id,
        falsification_criteria=list(spec.get("falsification_criteria", [])),
        stats_result=result,
        economic_threshold=(
            float(spec["economic_significance_threshold"])
            if spec.get("economic_significance_threshold") is not None
            else None
        ),
        declared_horizons=list(config.horizons),
    )
    transition = decide_transition(evaluation)

    return {
        "ref_id": str(ref_id),
        "hypothesis_id": hypothesis_id,
        "config_hash": config.config_hash(),
        "result": result,
        "evaluation": _evaluation_dict(evaluation),
        "transition": transition,
        "family_id": family_id,
    }


def _embargo_bars(config: ExperimentConfig) -> int:
    """E = H (spec 8.1 step 2): the embargo is derived from the horizon, never
    a hidden default (PD-11)."""
    return max(config.horizons)


def _persist_stats_result(
    conn: Connection,
    *,
    hypothesis_id: str,
    config: ExperimentConfig,
    result: dict[str, Any],
    family_id: str | None,
) -> UUID:
    """Store the result in ``research.stats_result`` (the M5 contract: the
    result row carries the resolved params + the scope + the dataset
    version - the reproducibility metadata)."""
    ref_id = uuid.uuid4()
    # The M5 contract: the row is append-only and carries the reproducibility
    # metadata (resolved_params + dataset_version + code_version). The M5 API
    # stores experiment_id NULL and keeps the scope/grade in the result JSON -
    # the HF follows the SAME schema (HF-001: never a parallel table shape).
    conn.execute(
        text(
            "INSERT INTO research.stats_result (ref_id, experiment_id, filter,"
            " data_scope, dataset_version, dataset_schema_version, engine_version,"
            " code_version, resolved_params, result, seed, supersedes, locked)"
            " VALUES (:ref_id, NULL, CAST(:filter AS jsonb), :data_scope,"
            " :dataset_version, :dataset_schema_version, :engine_version,"
            " :code_version, CAST(:resolved AS jsonb), CAST(:result AS jsonb),"
            " :seed, NULL, false)"
        ),
        {
            "ref_id": ref_id,
            "filter": _json(result["filter"]),
            "data_scope": config.data_scope,
            "dataset_version": result.get("dataset_version") or "unresolved",
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "engine_version": ENGINE_VERSION,
            "code_version": code_version(),
            "resolved": _json(
                {
                    "detector": {},
                    "stats": config.stats_params(),
                    "family_id": family_id,
                    "hypothesis_id": hypothesis_id,
                    "config_hash": config.config_hash(),
                }
            ),
            "result": _json(result),
            "seed": config.bootstrap_seed,
        },
    )
    return ref_id


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _evaluation_dict(evaluation: Evaluation) -> dict[str, Any]:
    return {
        "falsified": evaluation.falsified,
        "invalid": evaluation.invalid,
        "reject_reason": evaluation.reject_reason,
        "statistical_support": evaluation.statistical_support,
        "economic_only": evaluation.economic_only,
        "notes": list(evaluation.notes),
        "criteria": [
            {"criterion": c.criterion, "fired": c.fired, "detail": c.detail}
            for c in evaluation.criteria
        ],
    }


def experiment_config_for(
    conn: Connection,
    *,
    hypothesis_id: str,
    scope_family: str | None = None,
    data_scope: str = "DEVELOPMENT",
    bootstrap_seed: int | None = None,
) -> ExperimentConfig:
    """Resolve the experiment config for the hypothesis's current version
    (spec §10: the experiment inherits the version's declarations)."""
    hypothesis = get_hypothesis(conn, hypothesis_id)
    spec = hypothesis["spec"] or {}
    family = scope_family or _default_family()
    return build_experiment_config(
        hypothesis_id=hypothesis_id,
        version=int(spec.get("version", 1)),
        spec=spec,
        scope_family=family,
        data_scope=data_scope,
        bootstrap_seed=bootstrap_seed,
    )


def _default_family() -> str:
    from engine.scopes import default_family

    return default_family()
