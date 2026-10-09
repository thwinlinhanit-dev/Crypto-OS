"""Detector framework: contract, event object, ids (spec sections 6.1-6.2).

Design note specs/design/m4_events.md section 2 (decisions D-022..D-024).
Every detector is a pure function of (frame, resolved_params, exclusions);
parameters resolve through config/defaults.yaml only (PD-11); the event
carries full provenance (PD-5/PD-6) and ``event_id`` is content-addressed so
on-demand recompute is idempotent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import polars as pl

from engine.versions import ENGINE_VERSION

#: Section 6.2 direction vocabulary (R11: FAIL/TOP are research events, not
#: a strategy short side - the short *strategy* stays disabled until M10).
DIRECTIONS = ("LONG", "SHORT")

# Provenance classes (Master section 63).
OBSERVED = "OBSERVED"
CALCULATED = "CALCULATED"


def canonical_json(value: Any) -> str:
    """Deterministic JSON (sorted keys) used for params_hash and event_id."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def params_hash(resolved_params: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of resolved params (PD-6)."""
    return hashlib.sha256(canonical_json(resolved_params).encode()).hexdigest()


def event_time_from_open(open_time_ms: int, timeframe: str) -> datetime:
    """CLOSE time of the bar (spec 6.2): open + timeframe duration (M1 helper)."""
    from engine.data.bars import close_time_ms, parse_timeframe

    close_ms = close_time_ms(open_time_ms, parse_timeframe(timeframe))
    return datetime.fromtimestamp(close_ms / 1000.0, tz=UTC)


@dataclass(frozen=True)
class Event:
    """One emitted event (section 6.2). Frozen: append-only results (PD-13)."""

    detector_id: str
    event_type: str
    version: str
    params_hash: str
    instrument_id: str
    timeframe: str
    event_time: datetime
    direction: str
    ref_price: float
    invalidation_price: float
    regime: Mapping[str, Any]
    features: Mapping[str, Any]
    trigger: Mapping[str, Any]
    lookback_gap: bool
    dataset_version: str
    engine_version: str = ENGINE_VERSION
    data_quality: Mapping[str, Any] = field(default_factory=dict)
    resolved_params: Mapping[str, Any] = field(default_factory=dict)
    #: Bar index the event fired on (engine-internal; not part of the hash).
    event_index: int = -1

    @property
    def event_id(self) -> str:
        """sha1 over the spec's identity fields (section 6.2)."""
        basis = (
            f"{self.detector_id}|{self.version}|{self.params_hash}|"
            f"{self.instrument_id}|{self.timeframe}|{self.event_time.isoformat()}"
        )
        return hashlib.sha1(basis.encode()).hexdigest()

    def to_envelope(self) -> dict[str, Any]:
        """JSON-ready dict matching the section 6.2 API envelope."""
        return {
            "event_id": self.event_id,
            "detector_id": self.detector_id,
            "event_type": self.event_type,
            "version": self.version,
            "params_hash": self.params_hash,
            "instrument_id": self.instrument_id,
            "timeframe": self.timeframe,
            "event_time": self.event_time.isoformat(),
            "direction": self.direction,
            "ref_price": self.ref_price,
            "invalidation_price": self.invalidation_price,
            "regime": dict(self.regime),
            "features": dict(self.features),
            "trigger": dict(self.trigger),
            "lookback_gap": self.lookback_gap,
            "dataset_version": self.dataset_version,
            "engine_version": self.engine_version,
            "data_quality": dict(self.data_quality),
            "resolved_params": dict(self.resolved_params),
            "provenance": {
                "ref_price": OBSERVED,
                "event_time": OBSERVED,
                "invalidation_price": CALCULATED,
                "regime": CALCULATED,
                "features": CALCULATED,
                "trigger": CALCULATED,
            },
        }


@runtime_checkable
class Detector(Protocol):
    """Section 6.1 contract every registered detector satisfies."""

    detector_id: str
    version: str
    required_inputs: tuple[str, ...]
    warmup_bars: int
    lookback_bars: int
    default_cluster_model: str
    defaults_key: str

    def resolve(
        self,
        overrides: Mapping[str, Any] | None = None,
        defaults: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Parameters for one run (PD-11); raises ParamError/ValidationError."""
        ...

    def derived(
        self, frame: pl.DataFrame, resolved: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Every derived quantity, for detection and for the hover card."""
        ...

    def detect(
        self,
        frame: pl.DataFrame,
        resolved_params: Mapping[str, Any],
        exclusions: Sequence[Any] = (),
        *,
        instrument_id: str = "",
        dataset_version: str = "",
        data_quality: Mapping[str, Any] | None = None,
        timeframe: str = "1D",
    ) -> list[Event]:
        """Scan ``frame`` (closed bars only) and return events in bar order."""
        ...
