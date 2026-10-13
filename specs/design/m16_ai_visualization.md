# Visualization Runtime — M16 Design Note (AI Visualization)

**Source:** `specs/CRYPTOOS_PROGRAMMABLE_VISUALIZATION_RUNTIME_SPEC_v1.0.md` §25 AI generation, §26 MCP tools, §36 M16.

**M16 scope:** natural language builder · editor · validator · debugger · explanation. **Gate:** unsafe generated code is blocked and valid programs are reproducible.

**Builds on M12–M15**: the §6 program/lifecycle, the §9 data API, the §10 sandbox, the §22 provenance, the renderers, the canvas, microstructure, derivatives.

## The critical spec rules (§25)

> "AI cannot invent observed numbers, bypass permissions, silently substitute datasets, or hide model assumptions. Generated programs are untrusted until validated."

The §25 flow is the whole module:

```
User request → AI identifies required datasets → availability check →
assumption proposal → program generation → parse/type/security/resource
checks → fixture test → sandbox execution → render → explanation
```

M16 implements this flow as a **pure, deterministic pipeline** where the AI's output is a *proposal* that passes through the same validation a human's program does — the exact pattern the Hypothesis Factory established (every AI output is a proposal, never a finding).

## Reuse

| Need | Existing asset |
|---|---|
| untrusted-output validation | HF `propose_hypothesis` (every AI output is a proposal) + M12 `validate_program` (fail-closed) |
| the trust boundary | M9 `engine/ai/registry.py` closed registry + `NOT_IMPLEMENTED` + `canonical_ref_id` |
| sandbox execution | M12 `run_program` (§8 pipeline: validate → capabilities → declared datasets → as_of → compute) |
| forbidden-capability check | M12 `assert_no_forbidden_capabilities` (§10) |
| provenance | M12 `ProvenanceValue`/`classify` (§22); AI values are the `AI` class |
| the program hash | M12 `program_hash` (sha256 canonical JSON, D-083) |

## The new modules (engine/visualization/)

### `ai_generation.py` — the §25 pipeline (pure)

- **`GenerationRequest`** — the user's natural-language request + the declared parameters
- **`identify_datasets`** — step 2: the closed dataset vocabulary match (a request mentioning funding/OI/liquidations maps to the declared `required_datasets`); an unmappable request returns `[]` and is refused at the availability step (never a guessed dataset). Keywords shorter than 4 characters (`oi`, `cvd`) match on **word boundaries only** (D-106) — a substring match would map "point"/"voice" onto `open_interest`.
- **`availability_check`** — step 3: the declared datasets against what the sandbox can supply; a missing dataset is an explicit `DATA_UNAVAILABLE` refusal — **never a silent substitution** (§25)
- **`propose_assumptions`** — step 4: the model assumptions the program will make, as an explicit list (provenance `AI`); **hidden assumptions are refused**
- **`generate_program`** — step 5: the generated `VisualizationProgram`; it goes through the same `validate_program` a human's does (fail-closed: unknown dataset, forbidden permission, unknown render kind). The request's `params` are carried into `program.parameters` (§6) — never dropped.
- **`validate_generated`** — steps 6–7: the parse/type/security/resource checks + the **fixture test**. The security check re-runs the §10 forbidden-capability check; the resource check applies the §17.2 budget (declared + positive, §6); the fixture test runs the program through the M12 sandbox on deterministic **synthetic fixture rows** (§9 envelope, `GREEN`, `dataset_version="FIXTURE"` — an unmistakable non-manifest marker) with a structure-only render graph (`{"kind": <first declared output>}` — **no observed numbers**, PD-5). A program that cannot even execute on fixtures is blocked before any real data path (§25, D-109).
- **`explain`** — step 10: the explanation of what the program does, its datasets, its assumptions (provenance `AI`) and its provenance chain
- **`pipeline`** — the whole §25 flow, each step gated; the output carries the `ref_id` (§26's envelope: `ref_id, dataset_version, program_hash, runtime_version, data_quality, result`) and — since M16 — the **computed `program_hash`** of the generated program (the §36 reproducibility gate: the same request yields the same `program_hash`). `render` stays `null` for drafts: the fixture graph is an execution proof, not rendered output; real renders land with the §27 run endpoints (M17).

### `edit_program` — the M16 editor (§36; §6 semantics)

A program edit never mutates an existing version (the dataclass is frozen) and never lets behavior silently change:

- allowed edit keys are a **closed set** (`title, description, source, required_datasets, outputs, parameters, performance_budget, permissions`); an unknown key is refused (fail-closed, no silently-ignored edits)
- a **behavior-relevant** change (anything except `title`/`description`) requires an explicit **new version** — otherwise the edit is refused with §6's rule as the reason
- `visualization_id` can never change (identity is not editable)
- editing a `DEPRECATED` program is refused (terminal state)
- the edited program re-enters `validate_program` + `assert_no_forbidden_capabilities` before it is returned (D-102, untrusted until validated); a behavior edit returns the new version as `DRAFT`, a metadata-only edit preserves `base.status`

### `debug_program` — the M16 debugger (§36; §32 semantics)

`debug_program(program, *, datasets, available)` runs every §25 check **without touching real data or mutating any state** — the only execution is the fixture test on synthetic rows (D-109) — and returns a `DebugReport` of findings instead of raising. Each finding mirrors §32's per-state contract: `state` (from the §32 vocabulary: `PROGRAM_INVALID`, `PERMISSION_DENIED`, `DATA_UNAVAILABLE`, `RUNTIME_ERROR`), `what_happened`, `trustworthy` (always `false` — no output exists), `next_action`, and the refusal's `detail`. All checks are collected in the **same order `validate_generated` checks them** (a debugger reports *every* problem, not just the first), and the fixture test runs only after the static checks pass. A report with `ok=True` is exactly the condition under which `validate_generated` would not raise — the debugger never weakens the gate, it only explains it (D-108).

### The MCP tools (§26)

Registered into the existing M9 registry (extend, not replace — the HF pattern):
`list_visualizations, get_visualization, create_visualization_draft, validate_visualization, run_visualization, get_visualization_data, get_visualization_provenance, save_visualization, clone_visualization, compare_visualizations, get_market_canvas, get_chart_context`.

The **`VISUALIZATION_WRITE`** tier is new (§26: "Permission tiers: READ_ONLY, RESEARCH, VISUALIZATION_WRITE. No trading tier."). The M9 registry's `Tier` is a `Literal["READ_ONLY", "RESEARCH"]` — M16 **extends** it with `VISUALIZATION_WRITE` and **asserts no trading tier ever exists** (the §3 non-negotiable). The write tools (`create_visualization_draft, save_visualization, clone_visualization`) are VISUALIZATION_WRITE; the rest are READ_ONLY/RESEARCH.

## The gate

- **Unsafe generated code is blocked (§36 M16 gate):** a generated program requesting network/filesystem/trading, an undeclared dataset, an unknown render kind, an over-budget resource claim, or a hidden assumption is **refused before sandbox execution** — asserted by tests.
- **Valid programs are reproducible (the gate):** the same request → the same `program_hash` → the same render graph (the determinism test), and the §26 envelope carries the `ref_id`/`program_hash`/`runtime_version`.

## Deferred (M17+ / spec §38.10)

- The **live LLM provider** (the `EchoProvider` is the test default; the real provider is the OQ-25 owner input — the M9 pattern).
- The **research bridge** (M17: visualization → hypothesis, experiment linkage, chart snapshots, feature extraction).
- The **§28 DB tables** + the API (M17's slice).

## Decisions

- D-102: a generated visualization program is untrusted until validated — it passes the same `validate_program` + `assert_no_forbidden_capabilities` a human's does (the HF proposal pattern). The editor (`edit_program`) re-applies the same rule to edited programs.
- D-103: the M16 tier vocabulary extends M9's `READ_ONLY/RESEARCH` with `VISUALIZATION_WRITE`; a trading tier is asserted never to exist (§3/§26). The tier reaches both public surfaces (`engine/ai/mcp.py` `TIERS`, `GET /v1/ai/tools`).
- D-104: the §26 tool-result envelope (`ref_id, dataset_version, program_hash, runtime_version, data_quality, result`) is mandatory on every tool result; the `ref_id` is the M9 `canonical_ref_id` (sha256, process-stable).
- D-105: M16 ships the pure pipeline + the registry extension; the live provider, the research bridge, and the §28 DB tables are M17+.
- D-106: dataset keywords shorter than 4 characters (`oi`, `cvd`) match on word boundaries only — a substring match maps "point"/"voice" onto `open_interest`, which would be a guessed dataset (§25); longer keywords keep substring matching so plurals ("candles", "liquidations") still map.
- D-107: the M16 editor is a **version-gated re-validation seam**, not a free mutator: behavior-relevant edits require an explicit new version (§6 "Behavior cannot silently change under an existing version"), `DEPRECATED` is terminal (§6), and the edited program re-enters validation. The spec names "editor" (§36) without defining its UX; this decision pins the engine-level contract only — the editing UI/API surface is M17's slice (§27 has no editor endpoint).
- D-108: the M16 debugger (`debug_program`) is a **read-only diagnostic** over the same §25 checks — it collects findings instead of raising and never executes a program (§32: each state states what happened, whether the output is trustworthy, and the next user action). `ok=True` iff `validate_generated` would not raise; the debugger can never weaken the gate.
- D-109: the §25 **fixture test** runs the generated program through the M12 sandbox on deterministic synthetic rows with `dataset_version="FIXTURE"` and a structure-only render graph (first declared output kind, no observed numbers). Draft results carry `render: null` — the fixture graph is an execution proof, not rendered output (PD-5); real renders land with the §27 run endpoints (M17).
- D-110: `program.as_dict()` includes the computed `program_hash` (§6 required metadata) so `run_program`'s result and the §26 envelope carry a real hash instead of `null`; `GenerationRequest.params` flows into `program.parameters` — request parameters are never silently dropped (PD-11).
