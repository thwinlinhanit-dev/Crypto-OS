# M9 design note — AI Copilot + MCP

Companion to IMPLEMENTATION_SPEC_v1.1.1 Section 9 (AI), Section 10
(`agents.run` / `agents.tool_call`), Section 11 (`/v1/ai/chat`,
`/v1/ai/tools`, `/v1/agents/runs`) and Section 15 (milestone M9).

Decisions recorded in `docs/DECISIONS.md` as D-059..D-066; open questions
OQ-25 (OD-4).

> Scope discipline (PD-9): M9 builds the Copilot tool registry, the
> number-integrity placeholder resolver, the MCP server (READ_ONLY +
> RESEARCH tiers) and the audit trail. The live LLM provider is selected by
> env (OD-4, `LLM_PROVIDER`); until the owner names one, the provider
> registry ships with a deterministic `EchoProvider` for tests and the
> OpenAI-compatible adapter is behind the same interface.

---

## 0. Acceptance-criteria mapping

| AC (M9) | Where it lands |
| --- | --- |
| numbers without ref flagged | `engine/ai/integrity.py` — the placeholder resolver + the free-text scanner; `tests/test_ai_integrity.py` |
| MCP calls audited | `engine/ai/mcp.py` + migration 0009 (`agents.run`, `agents.tool_call`); every call written to both tables; `tests/test_ai_mcp.py` |
| tool-registry test asserts no order-related tools and that experiment tools respect immutability | `tests/test_ai_registry.py` — the AC's explicit gate |

---

## 1. Engine layout

```
engine/ai/
  registry.py    the tool registry (20 tools, tiers, JSON schemas)
  integrity.py   {{ref:<ref_id>#<json_path>}} resolution + free-text scan
  providers.py   LLMProvider interface + EchoProvider + the OpenAI adapter
  runner.py      the Copilot loop (mode -> system prompt + tool subset)
  mcp.py         the MCP server (stdio + HTTP, READ_ONLY + RESEARCH)
```

## 2. The tool registry (spec §9)

The 20 tools, deterministic JSON, each result carrying `ref_id` +
`dataset_version` (PD-5/PD-6):

- **READ_ONLY** (the MCP read tier): `search_instruments`, `get_candles`,
  `get_data_health`, `search_events`, `get_event`, `get_stats`,
  `list_hypotheses`, `get_experiment`, `get_lineage`, `get_backtest`,
  `get_validation`, `get_chart_context`.
- **RESEARCH** (the MCP research tier, adds writes): `compute_stats`,
  `compare_event_sets`, `create_hypothesis`, `create_experiment`,
  `create_note`, `create_report`, `run_backtest`, `run_validation`,
  `run_random_baseline`, `freeze_experiment`.

- **No order-related tools exist** (R6/PD-1: research only, no order path).
  The registry test asserts it.
- **Holdout guard** (spec: "No tool may take a holdout-scope action without
  the open-holdout flow"): every tool that could touch HOLDOUT scope
  (`compute_stats`, `run_backtest`, `run_validation`) requires an opened
  holdout for the target hypothesis, else raises the 409 the M5 gate
  already implements (D-030).
- **Immutability guard** (spec: "AI may not modify frozen objects"):
  `freeze_experiment` is allowed (it is the freeze), but `create_experiment`
  on a frozen experiment's follow-ups must use `supersedes`; `run_backtest`
  on a frozen experiment is allowed (backtests are append-only and belong to
  an experiment), while any UPDATE-shaped call is absent by construction.
  The registry test asserts the frozen-object contract explicitly.

## 3. Number integrity (spec §9, enforced in code)

- The model emits numbers only as placeholders
  `{{ref:<ref_id>#<json_path>}}`; `resolve_placeholders(text, tool_results)`
  substitutes each from the stored tool result and **fails closed** on an
  unknown ref or path (D-059: a placeholder that cannot be resolved is
  removed and flagged, never rendered as a raw number).
- `scan_free_text(text)` finds raw numerals in free text (outside
  placeholders) and returns them; the UI flags them and the run logs them
  (the AC: "numbers without ref flagged").
- Reports follow the Master §66 shape plus: n_eff, intervals, data_scope,
  OOS, the multiple-testing block, limitations; grade wording per 8.3.
- Critic mode must list at least three ways the conclusion could be wrong
  (D-060: the runner enforces a minimum of three critique items, else the
  mode fails closed).

## 4. Providers (spec §9, OD-4)

`LLMProvider.stream_chat(messages, tools)` is the single interface; the
adapter is selected by `LLM_PROVIDER` env (D-061: the env var is the only
selection source — PD-11). Until the owner names one (OD-4/OQ-25):

- `EchoProvider` (deterministic, tests) replays the tool calls it is given;
- `OpenAIProvider` (OpenAI-compatible chat completions with tool calling)
  is behind the same interface, key from the env (PD-8: no keys in repo).

## 5. MCP server (spec §9)

- Exposes the READ_ONLY + RESEARCH tools over MCP (stdio + HTTP), same
  schemas; every call is written to `agents.tool_call` **and**
  `system.audit_log` (the AC: "MCP calls audited").
- Tiers in v1: READ_ONLY, RESEARCH. Order tools do not exist — the server
  refuses any request naming an unknown tool (fail-closed, PD-4).
- Migration 0009 transcribes `agents.run` + `agents.tool_call` from §10.

## 6. API (spec 11)

```
POST /v1/ai/chat        (SSE stream; {messages, mode})
GET  /v1/ai/tools       (the registry as JSON: name, tier, schema)
GET  /v1/agents/runs    (list)
GET  /v1/agents/runs/{id} (one + its tool_calls)
```

Fail-closed: unknown mode 422, unknown tool 422, a holdout-scope action
without an opened holdout 409, any attempt to touch a frozen object 409.

## 7. Out of scope (parked)

- The live provider selection (OD-4/OQ-25) — the owner names it; the
  interface and the Echo/OpenAI adapters ship now.
- Streaming UI polish (the SSE endpoint exists; the Copilot panel is the
  M9 UI slice that lands with the provider choice).
- v1.1 agent modes beyond the five (Research, Quant, Data, Critic, Writer).

## 8. Test plan (tests first)

| Suite | Covers |
| --- | --- |
| `tests/test_ai_registry.py` | the 20 tools + tiers; **no order-related tools**; experiment tools respect immutability (the AC gate) |
| `tests/test_ai_integrity.py` | placeholder resolution (incl. fail-closed), free-text numeral flagging |
| `tests/test_ai_providers.py` | the LLMProvider interface; EchoProvider determinism; env selection (PD-11) |
| `tests/test_ai_mcp.py` | stdio+HTTP tool calls audited to both tables; unknown tool refused |

## 9. Decisions to record

| ID | Decision |
| --- | --- |
| D-059 | Unresolvable placeholders are removed and flagged, never rendered raw |
| D-060 | Critic mode fails closed below three critique items |
| D-061 | The provider is selected by `LLM_PROVIDER` env only (PD-11) |
| D-062 | Every MCP call writes agents.tool_call + system.audit_log (one row each) |
| D-063 | The tool registry is closed: unknown tools are 422, order tools do not exist |
| D-064 | Holdout-scope tools require an opened holdout (the M5 409 gate) |
| D-065 | The Copilot loop is a pure function over (messages, mode, tools) with the provider injected |
| D-066 | Tool results carry ref_id + dataset_version by construction (PD-5/PD-6) |

Open questions for the owner: **OQ-25** (= OD-4: the default LLM
provider/model — blocks the live Copilot only; the registry, integrity,
MCP and audit ship without it).
