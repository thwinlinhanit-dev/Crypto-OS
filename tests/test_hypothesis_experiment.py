"""HF Phase 3 — the experiment builder tests (spec §10).

The builder translates a hypothesis version into the resolved experiment
config the M5 engine consumes: declared method, declared horizon, declared
direction, no retrofitted choices, and a deterministic config hash.
"""

from __future__ import annotations

from typing import Any

import pytest

from engine.errors import ValidationError
from engine.hypothesis.experiment import (
    DEFAULT_CLUSTER_MODELS,
    build_experiment_config,
    scope_universe_guard,
)

DEFAULTS: dict[str, Any] = {
    "stats": {
        "horizons": [1, 3, 5, 10, 20, 40],
        "bootstrap_resamples": 5000,
        "bootstrap_seed": 20260101,
        "cluster_models_by_detector": {},
    }
}


def _spec(**over: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "null_hypothesis": "no relationship",
        "alternative_hypothesis": "a relationship",
        "independent_vars": ["x"],
        "dependent_vars": ["y"],
        "features": ["rvol"],
        "population": "synthetic",
        "timeframe": "1D",
        "observation_ts_rule": "bar close_time",
        "outcome_horizon": "5 bars",
        "expected_direction": "HIGHER",
        "data_requirements": ["market.funding_rate"],
        "proposed_statistical_method": "block-bootstrap mean difference",
        "falsification_criteria": ["effect vanishes after fees"],
    }
    spec.update(over)
    return spec


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_the_config_inherits_the_declared_method_and_horizons() -> None:
    config = build_experiment_config(
        hypothesis_id="h",
        version=1,
        spec=_spec(),
        scope_family="binance_spot_usdt_default",
        defaults=DEFAULTS,
    )
    assert config.method == "block-bootstrap mean difference"
    assert config.expected_direction == "HIGHER"
    assert config.horizons == (1, 3, 5, 10, 20, 40)
    assert config.cluster_models == DEFAULT_CLUSTER_MODELS
    assert config.bootstrap_resamples == 5000
    assert config.data_scope == "DEVELOPMENT"


def test_the_declared_horizon_is_the_anchor() -> None:
    """The hypothesis's declared outcome horizon must exist in the
    configured list (it anchors the analysis)."""
    config = build_experiment_config(
        hypothesis_id="h",
        version=1,
        spec=_spec(outcome_horizon="10 bars"),
        scope_family="f",
        defaults=DEFAULTS,
    )
    assert 10 in config.horizons


def test_the_stats_params_shape_matches_the_m5_engine() -> None:
    config = build_experiment_config(
        hypothesis_id="h", version=1, spec=_spec(), scope_family="f", defaults=DEFAULTS
    )
    params = config.stats_params()
    assert params["horizons"] == [1, 3, 5, 10, 20, 40]
    assert params["bootstrap_resamples"] == 5000
    assert isinstance(params["bootstrap_seed"], int)


# --------------------------------------------------------------------------
# The determinism contract (spec §1.2.3)
# --------------------------------------------------------------------------


def test_the_config_hash_is_deterministic() -> None:
    a = build_experiment_config(
        hypothesis_id="h", version=1, spec=_spec(), scope_family="f", defaults=DEFAULTS
    )
    b = build_experiment_config(
        hypothesis_id="h", version=1, spec=_spec(), scope_family="f", defaults=DEFAULTS
    )
    assert a.config_hash() == b.config_hash()


def test_the_config_hash_changes_with_the_hypothesis_version() -> None:
    a = build_experiment_config(
        hypothesis_id="h", version=1, spec=_spec(), scope_family="f", defaults=DEFAULTS
    )
    b = build_experiment_config(
        hypothesis_id="h", version=2, spec=_spec(), scope_family="f", defaults=DEFAULTS
    )
    assert a.config_hash() != b.config_hash()


def test_the_config_hash_changes_with_the_seed() -> None:
    a = build_experiment_config(
        hypothesis_id="h", version=1, spec=_spec(), scope_family="f",
        bootstrap_seed=1, defaults=DEFAULTS,
    )
    b = build_experiment_config(
        hypothesis_id="h", version=1, spec=_spec(), scope_family="f",
        bootstrap_seed=2, defaults=DEFAULTS,
    )
    assert a.config_hash() != b.config_hash()


def test_the_config_hash_changes_with_the_scope() -> None:
    a = build_experiment_config(
        hypothesis_id="h", version=1, spec=_spec(), scope_family="f1", defaults=DEFAULTS
    )
    b = build_experiment_config(
        hypothesis_id="h", version=1, spec=_spec(), scope_family="f2", defaults=DEFAULTS
    )
    assert a.config_hash() != b.config_hash()


# --------------------------------------------------------------------------
# The fail-closed validation (PD-10)
# --------------------------------------------------------------------------


def test_an_unsupported_method_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        build_experiment_config(
            hypothesis_id="h",
            version=1,
            spec=_spec(proposed_statistical_method="my secret indicator"),
            scope_family="f",
            defaults=DEFAULTS,
        )
    assert "not a supported method" in excinfo.value.message


def test_a_bad_direction_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_experiment_config(
            hypothesis_id="h",
            version=1,
            spec=_spec(expected_direction="UP"),
            scope_family="f",
            defaults=DEFAULTS,
        )


def test_a_declared_horizon_outside_the_config_is_rejected() -> None:
    """The hypothesis cannot declare a horizon the stats engine is not
    configured for - no silent widening."""
    with pytest.raises(ValidationError) as excinfo:
        build_experiment_config(
            hypothesis_id="h",
            version=1,
            spec=_spec(outcome_horizon="99 bars"),
            scope_family="f",
            defaults=DEFAULTS,
        )
    assert "not in the configured horizon list" in excinfo.value.message


def test_an_unknown_cluster_model_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_experiment_config(
            hypothesis_id="h",
            version=1,
            spec=_spec(cluster_models=["MAGIC"]),
            scope_family="f",
            defaults=DEFAULTS,
        )


def test_a_bad_data_scope_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_experiment_config(
            hypothesis_id="h",
            version=1,
            spec=_spec(),
            scope_family="f",
            data_scope="MAYBE",
            defaults=DEFAULTS,
        )


def test_an_empty_horizon_list_in_config_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_experiment_config(
            hypothesis_id="h",
            version=1,
            spec=_spec(),
            scope_family="f",
            defaults={"stats": {"horizons": [], "bootstrap_resamples": 10,
                                "bootstrap_seed": 1}},
        )


def test_missing_stats_defaults_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_experiment_config(
            hypothesis_id="h", version=1, spec=_spec(), scope_family="f", defaults={}
        )


# --------------------------------------------------------------------------
# The universe guard (PD-10)
# --------------------------------------------------------------------------


def test_an_empty_universe_is_rejected() -> None:
    config = build_experiment_config(
        hypothesis_id="h", version=1, spec=_spec(), scope_family="f", defaults=DEFAULTS
    )
    with pytest.raises(ValidationError):
        scope_universe_guard(config, [])


def test_a_non_empty_universe_passes() -> None:
    config = build_experiment_config(
        hypothesis_id="h", version=1, spec=_spec(), scope_family="f", defaults=DEFAULTS
    )
    scope_universe_guard(config, ["BINANCE:BTCUSDT"])


# --------------------------------------------------------------------------
# The holdout scope
# --------------------------------------------------------------------------


def test_the_holdout_scope_is_explicitly_requested() -> None:
    """The holdout is a separate experiment (spec §14.1), never an implicit
    widening of the development run."""
    config = build_experiment_config(
        hypothesis_id="h",
        version=1,
        spec=_spec(),
        scope_family="f",
        data_scope="HOLDOUT",
        defaults=DEFAULTS,
    )
    assert config.data_scope == "HOLDOUT"
