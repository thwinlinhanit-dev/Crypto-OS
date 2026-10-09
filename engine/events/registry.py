"""Detector registry (spec section 6.5, design section 2.1).

Autoload every module under ``engine/events/detectors/`` that exposes a
``DETECTOR`` attribute. Keys are ``(detector_id, version)``; any rule, param
or default change bumps the version and the old version stays registered.
A module that fails to import is a build error, never a silent skip (PD-3).

The six ``SPEC_REQUIRED`` placeholders (FAST_RECLAIM, TOP_CAPIT, TOP_CLIMAX,
TOPFAIL, CAPITFAIL, CAPIT_RECLAIM) are deliberately NOT stubbed: a stub
module would be an invitation to implement them (PD-4).
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from engine.errors import ValidationError
from engine.events.base import Detector

_REGISTRY: dict[tuple[str, str], Detector] | None = None
_SPEC_REQUIRED: frozenset[str] = frozenset(
    {
        "FAST_RECLAIM",
        "TOP_CAPIT",
        "TOP_CLIMAX",
        "TOPFAIL",
        "CAPITFAIL",
        "CAPIT_RECLAIM",
    }
)


def _load() -> dict[tuple[str, str], Detector]:
    from engine.events import detectors as package

    registry: dict[tuple[str, str], Detector] = {}
    for module_info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{package.__name__}.{module_info.name}")
        detector = getattr(module, "DETECTOR", None)
        if detector is None:
            continue
        key = (detector.detector_id, detector.version)
        if key in registry:
            raise RuntimeError(
                f"duplicate detector key {key}: {module_info.name} collides with "
                f"an already-registered detector"
            )
        registry[key] = detector
    return registry


def _registry() -> dict[tuple[str, str], Detector]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load()
    return _REGISTRY


def known_detectors() -> tuple[tuple[str, str], ...]:
    """Registered ``(detector_id, version)`` pairs, sorted."""
    return tuple(sorted(_registry()))


def spec_required_names() -> frozenset[str]:
    """The six names that must never resolve (PD-4)."""
    return _SPEC_REQUIRED


def resolve_detector(
    detector_id: str, version: str | None = None
) -> Detector:
    """Look up a detector; latest registered version when ``version`` is None.

    Raises ``ValidationError`` for unknown ids, SPEC_REQUIRED ids, and
    unknown versions - fail-closed, never a default (PD-10/PD-11).
    """
    if detector_id in _SPEC_REQUIRED:
        raise ValidationError(
            f"detector '{detector_id}' is SPEC_REQUIRED and not implemented",
            detail={"detector_id": detector_id, "status": "SPEC_REQUIRED"},
        )
    registry = _registry()
    if version is not None:
        detector = registry.get((detector_id, version))
        if detector is None:
            raise ValidationError(
                f"unknown detector version '{detector_id}@{version}'",
                detail={
                    "detector_id": detector_id,
                    "version": version,
                    "known": [list(pair) for pair in known_detectors()],
                },
            )
        return detector
    candidates = [detector for (did, _v), detector in registry.items() if did == detector_id]
    if not candidates:
        raise ValidationError(
            f"unknown detector '{detector_id}'",
            detail={
                "detector_id": detector_id,
                "known": [list(pair) for pair in known_detectors()],
                "spec_required": sorted(_SPEC_REQUIRED),
            },
        )
    return max(candidates, key=lambda d: d.version)


def detector_params_key(detector: Detector) -> str:
    """The config/defaults.yaml section key for this detector (PD-11)."""
    return detector.defaults_key


def detect_with(
    detector_id: str,
    frame: Any,
    *,
    version: str | None = None,
    params: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
    exclusions: Any = (),
    instrument_id: str = "",
    dataset_version: str = "",
    data_quality: dict[str, Any] | None = None,
    timeframe: str = "1D",
) -> list[Any]:
    """Resolve the detector, its params, then run ``detect`` (one call path)."""
    detector = resolve_detector(detector_id, version)
    resolved = detector.resolve(params, defaults)
    return detector.detect(
        frame,
        resolved,
        exclusions,
        instrument_id=instrument_id,
        dataset_version=dataset_version,
        data_quality=data_quality,
        timeframe=timeframe,
    )
