"""HF Phase 3 — the experiment builder (spec §3/§10; design note
docs/hypothesis-factory/IMPLEMENTATION_PLAN.md Phase 3).

The builder translates a formally defined hypothesis version into the
**resolved experiment config** the M5/M6/M7 engines consume. It is pure
(D-042): no database, no clock, no RNG except through the declared seed.
The composition seam it produces (the resolved params + the config hash)
is what makes every experiment reproducible (spec §1.2.3).

The builder does NOT run the experiment - the job runner does (Phase 3's
second half). Separation means the config can be reviewed, hashed and
frozen before any engine executes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from engine.config.defaults import load_defaults
from engine.errors import ValidationError
from engine.hypothesis.models import DIRECTIONS

#: The default cluster models (spec §8.2: the detector's documented
#: assumption; a hypothesis may narrow it, never widen it silently).
DEFAULT_CLUSTER_MODELS: Final[tuple[str, ...]] = ("INDEPENDENT", "SYMBOL", "TIMEFRAME")

#: The statistical methods the builder accepts (spec §11.1: the method is
#: declared in the hypothesis, not chosen after the results).
SUPPORTED_METHODS: Final[frozenset[str]] = frozenset(
    {
        "block-bootstrap mean difference",
        "block bootstrap mean difference",
        "cluster-bootstrap mean difference",
        "wilson win-rate test",
        "event study (standardized)",
    }
)


@dataclass(frozen=True)
class ExperimentConfig:
    """The resolved, hashable experiment configuration (spec §10).

    ``config_hash`` is the sha256 of the canonical JSON of the resolved
    params + scope family + method: the same hypothesis version tested on
    the same scope with the same method always hashes identically, which is
    the reproducibility contract (spec §1.2.3).
    """

    hypothesis_id: str
    version: int
    scope_family: str
    data_scope: str
    horizons: tuple[int, ...]
    cluster_models: tuple[str, ...]
    bootstrap_resamples: int
    bootstrap_seed: int
    method: str
    expected_direction: str
    variant_count: int = 1
    selection_method: str = "PRE_SPECIFIED"
    extras: Mapping[str, Any] = field(default_factory=dict)

    def config_hash(self) -> str:
        canonical = json.dumps(
            {
                "hypothesis_id": self.hypothesis_id,
                "version": self.version,
                "scope_family": self.scope_family,
                "data_scope": self.data_scope,
                "horizons": list(self.horizons),
                "cluster_models": list(self.cluster_models),
                "bootstrap_resamples": self.bootstrap_resamples,
                "bootstrap_seed": self.bootstrap_seed,
                "method": self.method,
                "expected_direction": self.expected_direction,
                "variant_count": self.variant_count,
                "selection_method": self.selection_method,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def stats_params(self) -> dict[str, Any]:
        """The ``params`` mapping the M5 ``compute_stats`` consumes."""
        return {
            "horizons": list(self.horizons),
            "bootstrap_resamples": self.bootstrap_resamples,
            "bootstrap_seed": self.bootstrap_seed,
        }


def _require_horizon_list(value: Any) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValidationError("horizons must be a list of positive integers", {})
    horizons: list[int] = []
    for item in value:
        if type(item) is not int or item < 1:
            raise ValidationError(
                "every horizon must be a positive integer", {"horizon": item}
            )
        horizons.append(item)
    if not horizons:
        raise ValidationError("at least one horizon is required", {})
    return tuple(sorted(set(horizons)))


def build_experiment_config(
    *,
    hypothesis_id: str,
    version: int,
    spec: Mapping[str, Any],
    scope_family: str,
    data_scope: str = "DEVELOPMENT",
    bootstrap_seed: int | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> ExperimentConfig:
    """Build the resolved experiment config from a hypothesis version
    (spec §10: the experiment inherits the hypothesis's declared method,
    horizons and direction - never a retrofitted choice)."""
    doc = defaults if defaults is not None else load_defaults()
    stats = doc.get("stats") if isinstance(doc, Mapping) else None
    if not isinstance(stats, Mapping):
        raise ValidationError(
            "stats defaults are not configured in config/defaults.yaml",
            {"section": "stats"},
        )

    direction = str(spec.get("expected_direction", ""))
    if direction not in DIRECTIONS:
        raise ValidationError(
            "expected_direction must be HIGHER, LOWER or DIFFERENT",
            {"expected_direction": direction},
        )

    method = str(spec.get("proposed_statistical_method", ""))
    if method not in SUPPORTED_METHODS:
        raise ValidationError(
            "proposed_statistical_method is not a supported method",
            {"method": method, "supported": sorted(SUPPORTED_METHODS)},
        )

    # the horizons: the hypothesis's declared outcome horizon must map onto
    # the configured horizon list; the declared horizon is the anchor
    declared = spec.get("outcome_horizon")
    configured = _require_horizon_list(stats.get("horizons"))
    if declared is not None:
        declared_int = _parse_declared_horizon(declared)
        if declared_int not in configured:
            raise ValidationError(
                "the declared outcome horizon is not in the configured horizon list",
                {"declared": declared_int, "configured": list(configured)},
            )

    cluster_models_raw = spec.get("cluster_models")
    if cluster_models_raw is None:
        cluster_models = DEFAULT_CLUSTER_MODELS
    else:
        cluster_models = tuple(str(m) for m in cluster_models_raw)
        unknown = [m for m in cluster_models if m not in DEFAULT_CLUSTER_MODELS]
        if unknown:
            raise ValidationError(
                "unknown cluster model", {"unknown": unknown, "allowed": list(DEFAULT_CLUSTER_MODELS)}
            )

    if data_scope not in ("DEVELOPMENT", "HOLDOUT"):
        raise ValidationError(
            "data_scope must be DEVELOPMENT or HOLDOUT", {"data_scope": data_scope}
        )

    resamples = stats.get("bootstrap_resamples")
    if type(resamples) is not int or resamples < 1:
        raise ValidationError(
            "bootstrap_resamples must be a positive integer", {"resamples": resamples}
        )
    seed = bootstrap_seed if bootstrap_seed is not None else stats.get("bootstrap_seed")
    if type(seed) is not int:
        raise ValidationError("bootstrap_seed must be an integer", {"seed": seed})

    return ExperimentConfig(
        hypothesis_id=hypothesis_id,
        version=version,
        scope_family=scope_family,
        data_scope=data_scope,
        horizons=configured,
        cluster_models=cluster_models,
        bootstrap_resamples=resamples,
        bootstrap_seed=seed,
        method=method,
        expected_direction=direction,
    )


def _parse_declared_horizon(declared: Any) -> int:
    """The hypothesis's ``outcome_horizon`` field (e.g. ``"5 bars"``) parsed
    to its integer."""
    if isinstance(declared, int) and not isinstance(declared, bool):
        return declared
    text = str(declared).strip().split()
    if not text or not text[0].isdigit():
        raise ValidationError(
            "outcome_horizon must be an integer or '<n> bars'", {"outcome_horizon": declared}
        )
    return int(text[0])


def scope_universe_guard(config: ExperimentConfig, universe: Sequence[str]) -> None:
    """PD-10: the universe the runner resolves must match the scope family's
    frozen universe. The builder carries the family name; the runner checks
    the membership (a mismatch fails the experiment, never widens it)."""
    if not universe:
        raise ValidationError("the scope family resolved to an empty universe", {})
