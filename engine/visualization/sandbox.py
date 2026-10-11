"""M12 — the sandbox interface (spec §10).

The sandbox is the trust boundary. A program receives only its declared
capabilities: an undeclared dataset is refused before execution (§30
permission safety), and a forbidden permission (network/filesystem/trading)
is refused before the pipeline runs (§3).

The §8 worker/WASM backends are deferred (the MVP runs the pure functions);
``run_program`` is the deterministic pipeline: source → validate → data API
(as_of-gated) → compute → render graph. The compute step is a pure function
the caller supplies - the sandbox never lets a program reach the database,
the network, or the filesystem.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from engine.errors import ValidationError
from engine.visualization.data_api import (
    DatasetResponse,
    assert_datasets_declared,
    assert_quality_ok,
    truncate_as_of,
)
from engine.visualization.models import PERMISSION_KEYS
from engine.visualization.program import VisualizationProgram, validate_program


@dataclass(frozen=True)
class SandboxRequest:
    """The §10 sandbox request: the program + its declared capabilities."""

    program: VisualizationProgram
    params: Mapping[str, Any] = field(default_factory=dict)
    as_of: str | None = None


def assert_no_forbidden_capabilities(program: VisualizationProgram) -> None:
    """§10: a program requesting network/filesystem/trading is refused
    before execution (§3: no orders, no credentials, no DB, no arbitrary
    network/filesystem)."""
    for key in PERMISSION_KEYS:
        if program.permissions.get(key) is True:
            raise ValidationError(
                "forbidden capability requested (§3)",
                {"permission": key},
            )


def run_program(
    request: SandboxRequest,
    *,
    datasets: Mapping[str, DatasetResponse],
    compute: Callable[[Mapping[str, tuple[Mapping[str, Any], ...]]], dict[str, Any]],
) -> dict[str, Any]:
    """The deterministic §8 pipeline (the pure M12 slice).

    Steps, in order, each fail-closed:
    1. validate the program (the §6 metadata + the §3 permissions);
    2. refuse forbidden capabilities (§10);
    3. resolve only the DECLARED datasets (§30 permission safety);
    4. truncate every dataset at ``as_of`` (§23 replay);
    5. refuse an unusable dataset (§9 explicit failure);
    6. run the caller's pure ``compute``;
    7. return the render graph + the provenance of the run.
    """
    program = request.program
    validate_program(program)
    assert_no_forbidden_capabilities(program)

    # only the declared datasets are visible to the program
    available = dict(datasets)
    assert_datasets_declared(program.required_datasets, list(available))

    gated: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for name in program.required_datasets:
        response = available.get(name)
        if response is None:
            raise ValidationError(
                "declared dataset was not supplied to the sandbox",
                {"dataset": name},
            )
        assert_quality_ok(response.quality)
        gated[name] = truncate_as_of(response.rows, request.as_of)

    graph = compute(gated)
    if not isinstance(graph, Mapping) or "kind" not in graph:
        raise ValidationError(
            "compute must return a render graph with a kind", {}
        )
    return {
        "visualization_id": program.visualization_id,
        "version": program.version,
        "program_hash": program.as_dict().get("program_hash"),
        "as_of": request.as_of,
        "render": dict(graph),
    }
