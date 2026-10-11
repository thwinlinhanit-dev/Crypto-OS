"""M12 — the visualization runtime package (spec §1: a controlled
projection layer over the existing engines)."""

from engine.visualization.data_api import (
    DatasetResponse,
    assert_datasets_declared,
    quality_of,
    truncate_as_of,
)
from engine.visualization.models import (
    ALLOWED_LIFECYCLE_TRANSITIONS,
    DATASETS,
    LIFECYCLE,
    PERMISSION_KEYS,
    PROVENANCE_CLASSES,
    QUALITY_STATES,
    RENDER_KINDS,
    RUNTIME,
    assert_valid_dataset,
    assert_valid_lifecycle,
    assert_valid_lifecycle_transition,
    assert_valid_provenance,
    assert_valid_quality_state,
    assert_valid_render_kind,
    is_terminal_lifecycle,
)
from engine.visualization.program import (
    VisualizationProgram,
    program_hash,
    validate_program,
)
from engine.visualization.provenance import (
    ProvenanceValue,
    assert_distinguishable,
    classify,
)
from engine.visualization.render import (
    HudElement,
    LineSeries,
    TableRow,
    render_hud,
    render_line,
    render_table,
)
from engine.visualization.sandbox import (
    SandboxRequest,
    assert_no_forbidden_capabilities,
    run_program,
)

__all__ = [
    "ALLOWED_LIFECYCLE_TRANSITIONS",
    "DATASETS",
    "LIFECYCLE",
    "PERMISSION_KEYS",
    "PROVENANCE_CLASSES",
    "QUALITY_STATES",
    "RENDER_KINDS",
    "RUNTIME",
    "DatasetResponse",
    "HudElement",
    "LineSeries",
    "ProvenanceValue",
    "SandboxRequest",
    "TableRow",
    "VisualizationProgram",
    "assert_datasets_declared",
    "assert_distinguishable",
    "assert_no_forbidden_capabilities",
    "assert_valid_dataset",
    "assert_valid_lifecycle",
    "assert_valid_lifecycle_transition",
    "assert_valid_provenance",
    "assert_valid_quality_state",
    "assert_valid_render_kind",
    "classify",
    "is_terminal_lifecycle",
    "program_hash",
    "quality_of",
    "render_hud",
    "render_line",
    "render_table",
    "run_program",
    "truncate_as_of",
    "validate_program",
]
