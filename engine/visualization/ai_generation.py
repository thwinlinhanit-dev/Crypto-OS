"""M16 — the AI visualization generation pipeline (spec §25).

The §25 flow, each step gated:

    User request -> AI identifies required datasets -> availability check ->
    assumption proposal -> program generation -> parse/type/security/
    resource checks -> fixture test -> sandbox execution -> render ->
    explanation

The non-negotiable (§25): "AI cannot invent observed numbers, bypass
permissions, silently substitute datasets, or hide model assumptions.
Generated programs are untrusted until validated."

A generated program is a PROPOSAL (D-102): it passes the same
``validate_program`` + ``assert_no_forbidden_capabilities`` a human's does.
The provider is the M9 abstraction (the EchoProvider is the test default;
the live provider is the OQ-25 owner input).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

from engine.ai.handlers import canonical_ref_id
from engine.errors import ValidationError
from engine.visualization.models import is_terminal_lifecycle
from engine.visualization.program import VisualizationProgram, program_hash, validate_program
from engine.visualization.sandbox import (
    SandboxRequest,
    assert_no_forbidden_capabilities,
    run_program,
)

#: The §26 tier vocabulary (D-103). No trading tier ever exists (§3/§26).
TIERS: Final[tuple[str, ...]] = ("READ_ONLY", "RESEARCH", "VISUALIZATION_WRITE")

#: The dataset keywords the generator maps a request onto (the closed
#: vocabulary; an unmappable request is refused, never guessed).
_DATASET_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "candles": ("candle", "price", "ohlc", "close"),
    "funding": ("funding",),
    "open_interest": ("open interest", "oi"),
    "liquidations": ("liquidation",),
    "orderbook": ("orderbook", "order book", "depth", "liquidity"),
    "trades": ("trade", "cvd", "delta", "volume"),
}

#: Keywords shorter than this match on WORD BOUNDARIES only (D-106): a
#: substring match would map "point"/"voice" onto open_interest — a guessed
#: dataset the user never named (§25). Longer keywords keep substring
#: matching so plurals ("candles", "liquidations") still map.
_SHORT_KEYWORD_MAX: Final[int] = 3


@dataclass(frozen=True)
class GenerationRequest:
    """The user's natural-language request (spec §25)."""

    request: str
    visualization_id: str
    version: str = "1.0.0"
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    """The §25 pipeline outcome: the validated program + the envelope."""

    request: str
    datasets: tuple[str, ...]
    assumptions: tuple[str, ...]
    program: VisualizationProgram | None
    render: dict[str, Any] | None
    explanation: str
    ref_id: str
    program_hash: str | None = None
    dataset_version: str | None = None
    blocked: str | None = None

    def envelope(self) -> dict[str, Any]:
        """The §26 tool-result envelope (D-104): every tool result carries
        ref_id, dataset_version, program_hash, runtime_version, data_quality,
        result."""
        return {
            "ref_id": self.ref_id,
            "dataset_version": self.dataset_version,
            "program_hash": self.program_hash,
            "runtime_version": RUNTIME_VERSION,
            "data_quality": {},
            "result": {
                "request": self.request,
                "datasets": list(self.datasets),
                "assumptions": list(self.assumptions),
                "explanation": self.explanation,
                "blocked": self.blocked,
                "render": self.render,
            },
        }


RUNTIME_VERSION: Final[str] = "1.0.0"


def identify_datasets(request: str) -> tuple[str, ...]:
    """§25 step 2: the closed-vocabulary dataset match. An unmappable
    request returns () and is refused at the availability step - never a
    guessed dataset (§25: no silent substitution).

    Keywords shorter than 4 characters match on word boundaries only
    (D-106): a substring match would map "point"/"voice" onto
    open_interest - a dataset the user never named.
    """
    text = request.lower()
    found: list[str] = []
    for dataset, keywords in _DATASET_KEYWORDS.items():
        if any(_keyword_matches(keyword, text) for keyword in keywords):
            found.append(dataset)
    return tuple(sorted(set(found)))


def _keyword_matches(keyword: str, text: str) -> bool:
    if len(keyword) <= _SHORT_KEYWORD_MAX:
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword in text


def availability_check(
    datasets: Sequence[str],
    *,
    available: Sequence[str],
) -> tuple[str, ...]:
    """§25 step 3: the declared datasets against what the sandbox can supply.
    A missing dataset is an explicit refusal - never substituted (§25)."""
    available_set = set(available)
    return tuple(d for d in datasets if d not in available_set)


def propose_assumptions(request: str, datasets: Sequence[str]) -> tuple[str, ...]:
    """§25 step 4: the model assumptions, as an explicit list (provenance
    AI). Hidden assumptions are refused (§25: no hidden model assumptions)."""
    assumptions: list[str] = []
    if "liquidations" in datasets:
        assumptions.append(
            "the liquidation map is MODEL-DERIVED (the §16 OI-adjusted model)"
        )
    if "orderbook" in datasets:
        assumptions.append("the orderbook snapshot is immutable raw data (§19)")
    if not datasets:
        assumptions.append("no dataset was identified for this request")
    return tuple(assumptions)


def generate_program(
    request: GenerationRequest,
    datasets: Sequence[str],
) -> VisualizationProgram:
    """§25 step 5: the generated program. It goes through the same
    ``validate_program`` a human's does (D-102 - untrusted until
    validated)."""
    if not datasets:
        raise ValidationError(
            "no datasets were identified for the request (§25)",
            {"request": request.request},
        )
    program = VisualizationProgram(
        visualization_id=request.visualization_id,
        version=request.version,
        title=f"Generated: {request.request[:60]}",
        source=f"// generated for: {request.request}",
        required_datasets=tuple(datasets),
        outputs=("LINE",),
        created_by="ai",
        # §6 program metadata: the request's parameters are carried through
        # verbatim - never silently dropped (PD-11/D-110)
        parameters=dict(request.params),
        # §17.2: the resource check requires a declared budget; the generator
        # declares the default served budget (5000 bars), never an invented one
        performance_budget=5000,
    )
    validate_program(program)
    return program


def validate_generated(
    program: VisualizationProgram,
    *,
    datasets: Sequence[str],
    available: Sequence[str],
) -> None:
    """§25 steps 6-7: the parse/type/security/resource checks + the fixture
    test. Every check is fail-closed; a failure BLOCKS execution (§36 M16
    gate: unsafe generated code is blocked)."""
    # the security check: the §10 forbidden-capability boundary
    assert_no_forbidden_capabilities(program)
    # the availability check: no silent substitution
    missing = availability_check(datasets, available=available)
    if missing:
        raise ValidationError(
            "the sandbox cannot supply a declared dataset (§25: no silent substitution)",
            {"missing": list(missing)},
        )
    # the resource check: the declared performance budget (spec §17.2)
    if program.performance_budget is None:
        raise ValidationError(
            "a generated program must declare its performance budget (§17.2)",
            {},
        )
    # the fixture test: the program must EXECUTE through the sandbox before
    # it is trusted near any real data (§25 step 7, D-109)
    fixture_test(program)


#: The §25 fixture-test marker: fixture rows are synthetic and carry an
#: unmistakable non-manifest dataset_version (D-109) - no synthetic value can
#: ever be mistaken for an observed one (PD-5/§22).
FIXTURE_VERSION: Final[str] = "FIXTURE"


def fixture_datasets(datasets: Sequence[str]) -> dict[str, Any]:
    """The deterministic fixture rows the §25 fixture test runs on (D-109).

    Each declared dataset gets a GREEN, zero-gap response of synthetic rows
    whose ``ts`` is monotonic (so the §23 replay truncation has something to
    truncate). No observed number is ever produced from them.
    """
    responses: dict[str, Any] = {}
    for name in datasets:
        rows = tuple({"ts": f"2024-01-0{i + 1}T00:00:00Z"} for i in range(3))
        responses[name] = _fixture_response(name, rows)
    return responses


def _fixture_response(name: str, rows: tuple[Mapping[str, Any], ...]) -> Any:
    """One fixture dataset response (§9 envelope, GREEN, FIXTURE-marked)."""
    from engine.visualization.data_api import DatasetResponse

    return DatasetResponse(
        dataset=name,
        dataset_version=FIXTURE_VERSION,
        schema_version="1",
        source="synthetic-fixture",
        venue="SYNTHETIC",
        instrument_id="FIXTURE",
        timeframe="1D",
        as_of=None,
        quality={"state": "GREEN", "completeness": 1.0, "n_rows": len(rows)},
        rows=rows,
    )


def fixture_test(program: VisualizationProgram) -> dict[str, Any]:
    """§25 step 7: the fixture test - run the generated program through the
    M12 sandbox on synthetic fixture rows and require a render graph of the
    first declared output kind (D-109).

    The graph is STRUCTURE ONLY (``{"kind": <first declared output>}`` - no
    observed numbers, PD-5): it proves the program executes, it is not
    rendered output. A program that cannot execute on fixtures is blocked
    before it can reach a real data path.
    """
    request = SandboxRequest(program=program)
    datasets = fixture_datasets(program.required_datasets)
    if not program.outputs:
        raise ValidationError(
            "a fixture test needs at least one declared output (§6)",
            {"outputs": list(program.outputs)},
        )

    def _structure_only(gated: Mapping[str, tuple[Mapping[str, Any], ...]]) -> dict[str, Any]:
        return {"kind": program.outputs[0]}

    return run_program(request, datasets=datasets, compute=_structure_only)


def explain(result_datasets: Sequence[str], assumptions: Sequence[str]) -> str:
    """§25 step 10: the explanation (provenance AI) of what the program
    does, its datasets, its assumptions and its provenance chain."""
    parts = [f"This visualization reads {', '.join(result_datasets) or 'no datasets'}."]
    if assumptions:
        parts.append("Assumptions: " + "; ".join(assumptions) + ".")
    parts.append(
        "Observed values are OBSERVED; derived values are CALCULATED; model "
        "estimates are INFERRED; this explanation is AI (spec §22)."
    )
    return " ".join(parts)


# --------------------------------------------------------------------------
# The M16 editor (§36): a version-gated re-validation seam (D-107)
# --------------------------------------------------------------------------

#: The closed set of fields an edit may change. ``visualization_id`` is
#: identity and is never editable; ``version`` moves through the ``version``
#: argument (never a change key); ``status`` moves through the lifecycle.
EDITABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "title",
        "description",
        "source",
        "required_datasets",
        "outputs",
        "parameters",
        "performance_budget",
        "permissions",
    }
)

#: The fields that change BEHAVIOR (everything an edit may touch except the
#: display metadata). §6: "Behavior cannot silently change under an existing
#: version" — a behavior edit requires an explicit new version.
_BEHAVIOR_FIELDS: Final[frozenset[str]] = EDITABLE_FIELDS - {"title", "description"}

#: The fields holding sequences the frozen dataclass stores as tuples.
_SEQUENCE_FIELDS: Final[frozenset[str]] = frozenset({"required_datasets", "outputs"})


def edit_program(
    base: VisualizationProgram,
    changes: Mapping[str, Any],
    *,
    version: str | None = None,
) -> VisualizationProgram:
    """The M16 editor: apply a fail-closed edit to a program (§36, §6).

    The rules, in order (D-107):
    1. ``visualization_id`` is identity and cannot be edited;
    2. an unknown field is refused — no silently-ignored edits (PD-4);
    3. a behavior-relevant change (anything beyond title/description)
       requires an explicit **new version** — otherwise behavior would
       silently change under an existing version (§6);
    4. a ``DEPRECATED`` program is terminal and cannot be edited (§6);
    5. the edited program re-enters ``validate_program`` +
       ``assert_no_forbidden_capabilities`` before it is returned — an edit
       is untrusted until validated, exactly like a generated program
       (D-102); a behavior edit lands as ``DRAFT``, a metadata-only edit
       preserves ``base.status``.
    """
    if "visualization_id" in changes:
        raise ValidationError(
            "visualization_id is program identity and cannot be edited",
            {"visualization_id": base.visualization_id},
        )
    if "version" in changes:
        raise ValidationError(
            "pass the new version through the version argument, not as a change",
            {"version": changes["version"]},
        )
    unknown = sorted(set(changes) - EDITABLE_FIELDS)
    if unknown:
        raise ValidationError(
            "unknown program edit field",
            {"unknown": unknown, "allowed": sorted(EDITABLE_FIELDS)},
        )
    if is_terminal_lifecycle(base.status):
        raise ValidationError(
            f"a {base.status} program cannot be edited (terminal lifecycle, §6)",
            {"status": base.status},
        )

    behavior_changed = bool(set(changes) & _BEHAVIOR_FIELDS)
    bumped = version is not None and version != base.version
    if behavior_changed and not bumped:
        raise ValidationError(
            "a behavior edit requires a new version "
            "(§6: behavior cannot silently change under an existing version)",
            {"behavior_fields": sorted(set(changes) & _BEHAVIOR_FIELDS)},
        )

    replacements: dict[str, Any] = {}
    for key, value in changes.items():
        if (
            key in _SEQUENCE_FIELDS
            and isinstance(value, Sequence)
            and not isinstance(value, (str, bytes))
        ):
            replacements[key] = tuple(value)
        else:
            replacements[key] = value
    if bumped:
        # a new version starts unvalidated: it re-enters the lifecycle at DRAFT
        replacements["version"] = version
        replacements["status"] = "DRAFT"

    edited = replace(base, **replacements)
    # D-102: the edit re-enters the same validation a human's program does
    assert_no_forbidden_capabilities(edited)
    validate_program(edited)
    return edited


# --------------------------------------------------------------------------
# The M16 debugger (§36): a read-only diagnostic over the same §25 checks
# (D-108) — it collects findings instead of raising and never executes a
# program on real data.
# --------------------------------------------------------------------------

#: The §32 failure-state vocabulary this diagnostic reports. Each state names
#: what happened, whether the output is trustworthy, and the next action.
DEBUG_STATES: Final[tuple[str, ...]] = (
    "PROGRAM_INVALID",
    "PERMISSION_DENIED",
    "DATA_UNAVAILABLE",
    "RUNTIME_ERROR",
)


@dataclass(frozen=True)
class DebugFinding:
    """One §32 finding: what happened, trustworthiness, next user action."""

    state: str
    what_happened: str
    trustworthy: bool
    next_action: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "what_happened": self.what_happened,
            "trustworthy": self.trustworthy,
            "next_action": self.next_action,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class DebugReport:
    """The §32 debugger report: every finding, and ok iff nothing failed.

    ``ok=True`` is exactly the condition under which ``validate_generated``
    would not raise — the debugger never weakens the gate, it only
    explains it (D-108).
    """

    findings: tuple[DebugFinding, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "findings": [f.as_dict() for f in self.findings]}


def debug_program(
    program: VisualizationProgram,
    *,
    datasets: Sequence[str],
    available: Sequence[str],
) -> DebugReport:
    """The M16 debugger: run every §25 check WITHOUT raising and collect the
    findings (§36, §32, D-108).

    The checks run in the same order ``validate_generated`` runs them — the
    security check, then the availability check, then the resource check —
    and the fixture test runs only after the static checks pass. A finding
    is never trustworthy: no output exists for a program that failed a
    gate. The debugger reads nothing and mutates nothing.
    """
    findings: list[DebugFinding] = []

    try:
        assert_no_forbidden_capabilities(program)
    except ValidationError as exc:
        findings.append(
            DebugFinding(
                state="PERMISSION_DENIED",
                what_happened=exc.message,
                trustworthy=False,
                next_action=(
                    "remove the forbidden capability (network/filesystem/trading) "
                    "from the program permissions (§3); a visualization program "
                    "can never request it"
                ),
                detail=exc.detail,
            )
        )

    missing = availability_check(datasets, available=available)
    if missing:
        findings.append(
            DebugFinding(
                state="DATA_UNAVAILABLE",
                what_happened=(
                    "the sandbox cannot supply the declared datasets: "
                    f"{', '.join(missing)} (§25: no silent substitution)"
                ),
                trustworthy=False,
                next_action=(
                    "declare only datasets the sandbox supplies, or wire the "
                    "dataset provider — never silently substitute a dataset (§25)"
                ),
                detail={"missing": list(missing)},
            )
        )

    if program.performance_budget is None:
        findings.append(
            DebugFinding(
                state="PROGRAM_INVALID",
                what_happened="a generated program must declare its performance budget (§17.2)",
                trustworthy=False,
                next_action="declare a positive performance_budget (§6/§17.2)",
                detail={},
            )
        )

    if not findings:
        # the fixture test runs only after the static checks pass (D-108);
        # it executes on synthetic fixture rows, never real data.
        try:
            fixture_test(program)
        except ValidationError as exc:
            findings.append(
                DebugFinding(
                    state="RUNTIME_ERROR",
                    what_happened=exc.message,
                    trustworthy=False,
                    next_action=(
                        "fix the program so it executes on the fixture test; a "
                        "program that cannot run is blocked before any real data"
                    ),
                    detail=exc.detail,
                )
            )

    return DebugReport(findings=tuple(findings))


def pipeline(
    request: GenerationRequest,
    *,
    available_datasets: Sequence[str],
) -> GenerationResult:
    """The full §25 flow, each step gated (the M16 gate: unsafe generated
    code is blocked; valid programs are reproducible).

    The success path carries the §26 envelope fields plus the program's
    computed ``program_hash`` (§36 gate: the same request yields the same
    hash). ``render`` stays ``null`` on drafts: the fixture test is an
    execution proof, not rendered output — the real render lands with the
    §27 run endpoints (D-109).
    """
    datasets = identify_datasets(request.request)
    if not datasets:
        return GenerationResult(
            request=request.request,
            datasets=(),
            assumptions=(),
            program=None,
            render=None,
            explanation="No dataset could be identified for this request.",
            ref_id=canonical_ref_id({"request": request.request, "blocked": "no-datasets"}),
            blocked="no-datasets",
        )
    missing = availability_check(datasets, available=available_datasets)
    if missing:
        return GenerationResult(
            request=request.request,
            datasets=tuple(datasets),
            assumptions=(),
            program=None,
            render=None,
            explanation=(
                "The request needs datasets the sandbox cannot supply: "
                f"{', '.join(missing)}."
            ),
            ref_id=canonical_ref_id({"request": request.request, "blocked": "unavailable"}),
            dataset_version=None,
            blocked="unavailable",
        )
    assumptions = propose_assumptions(request.request, datasets)
    program = generate_program(request, datasets)
    validate_generated(program, datasets=datasets, available=available_datasets)

    # D-110: the §36 reproducibility gate carries the program's computed hash
    # (§6 required metadata) on the draft and in the envelope.
    return GenerationResult(
        request=request.request,
        datasets=tuple(datasets),
        assumptions=assumptions,
        program=program,
        render=None,
        explanation=explain(datasets, assumptions),
        ref_id=canonical_ref_id(
            {
                "request": request.request,
                "datasets": list(datasets),
                "program": program.as_dict(),
            }
        ),
        program_hash=program_hash(
            program.source,
            visualization_id=program.visualization_id,
            version=program.version,
            runtime=program.runtime,
        ),
        dataset_version=None,
    )
