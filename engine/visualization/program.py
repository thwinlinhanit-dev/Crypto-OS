"""M12 — the versioned visualization program + its hash (spec §6).

A program is a versioned first-class object. ``program_hash`` is the sha256
of the canonical JSON (D-078): the same program always hashes identically
across processes - never Python's ``hash()`` (process-randomized; the HF
ref_id lesson).

``validate_program`` is fail-closed (§3): a program that declares a dataset
outside the vocabulary, a forbidden permission, or an unknown render kind
is refused before it is stored.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from engine.errors import ValidationError
from engine.visualization.models import (
    PERMISSION_KEYS,
    RENDER_KINDS,
    RUNTIME,
    assert_valid_dataset,
    assert_valid_lifecycle,
)

#: The §6 required metadata fields (a program missing one is underspecified).
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "visualization_id",
    "version",
    "title",
    "source",
    "required_datasets",
    "outputs",
    "created_by",
)


@dataclass(frozen=True)
class VisualizationProgram:
    """The §6 program: the versioned, hashable, permissioned object."""

    visualization_id: str
    version: str
    title: str
    source: str
    required_datasets: tuple[str, ...]
    outputs: tuple[str, ...]
    created_by: str
    description: str | None = None
    runtime: str = RUNTIME
    status: str = "DRAFT"
    permissions: Mapping[str, bool] = field(
        default_factory=lambda: {"network": False, "filesystem": False, "trading": False}
    )
    parameters: Mapping[str, Any] = field(default_factory=dict)
    performance_budget: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "visualization_id": self.visualization_id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "runtime": self.runtime,
            "status": self.status,
            "required_datasets": list(self.required_datasets),
            "outputs": list(self.outputs),
            "permissions": dict(self.permissions),
            "parameters": dict(self.parameters),
            "performance_budget": self.performance_budget,
            "created_by": self.created_by,
        }


def program_hash(
    source: str,
    *,
    visualization_id: str,
    version: str,
    runtime: str = RUNTIME,
) -> str:
    """The §6 program_hash: sha256 of the canonical JSON (D-078).

    Deterministic across processes: the same (source, id, version, runtime)
    tuple always hashes identically, which is the reproducibility contract
    (a version's behavior cannot silently change).
    """
    canonical = json.dumps(
        {
            "visualization_id": visualization_id,
            "version": version,
            "runtime": runtime,
            "source": source,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_program(program: VisualizationProgram) -> None:
    """Fail-closed validation (§3/§6): refuse an underspecified, over- or
    mis-permissioned program before it is stored."""
    missing = [
        field_name
        for field_name in REQUIRED_FIELDS
        if not getattr(program, field_name, None)
    ]
    if missing:
        raise ValidationError(
            "visualization program is underspecified",
            {"missing_fields": missing},
        )
    if not program.required_datasets:
        raise ValidationError(
            "a program must declare at least one required dataset", {}
        )
    for dataset in program.required_datasets:
        assert_valid_dataset(dataset)
    if not program.outputs:
        raise ValidationError("a program must declare at least one output", {})
    for output in program.outputs:
        if output not in RENDER_KINDS:
            raise ValidationError(
                "unknown output render kind",
                {"output": output, "allowed": list(RENDER_KINDS)},
            )
    assert_valid_lifecycle(program.status)

    # the §3 permissions: network/filesystem/trading may be declared false;
    # a request for true is refused (a visualization program cannot place
    # orders, touch credentials, the DB, arbitrary network or the
    # filesystem).
    for key in PERMISSION_KEYS:
        if key not in program.permissions:
            raise ValidationError(
                "program permissions must declare every capability",
                {"missing_permission": key, "required": list(PERMISSION_KEYS)},
            )
        if program.permissions[key] is True:
            raise ValidationError(
                "forbidden permission requested (§3)",
                {"permission": key},
            )

    # the version is a semantic version string; an empty one is refused
    if not isinstance(program.version, str) or not program.version.strip():
        raise ValidationError("version must be a non-empty string", {})

    # the performance budget, when declared, must be a positive integer
    if program.performance_budget is not None:
        budget = program.performance_budget
        if type(budget) is not int or budget < 1:
            raise ValidationError(
                "performance_budget must be a positive integer",
                {"performance_budget": budget},
            )
