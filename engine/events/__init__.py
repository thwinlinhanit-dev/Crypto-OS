"""Event engine: framework, cooldown, registry, detectors, outcomes (M4).

Public surface re-exports the section 6.1-6.2 contract plus the shared
cooldown gate and the section 6.4 outcome computer.
"""

from engine.events.base import (
    CALCULATED,
    DIRECTIONS,
    OBSERVED,
    Detector,
    Event,
    canonical_json,
    event_time_from_open,
    params_hash,
)
from engine.events.cooldown import apply_cooldown
from engine.events.outcomes import (
    DEFAULT_HORIZONS,
    HorizonOutcome,
    Outcome,
    compute_outcomes,
)
from engine.events.registry import (
    known_detectors,
    resolve_detector,
    spec_required_names,
)

__all__ = [
    "CALCULATED",
    "DEFAULT_HORIZONS",
    "DIRECTIONS",
    "OBSERVED",
    "Detector",
    "Event",
    "HorizonOutcome",
    "Outcome",
    "apply_cooldown",
    "canonical_json",
    "compute_outcomes",
    "event_time_from_open",
    "known_detectors",
    "params_hash",
    "resolve_detector",
    "spec_required_names",
]
