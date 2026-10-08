# M0 — Monorepo Bootstrap (design note)

Spec source: `IMPLEMENTATION_SPEC_v1.1.1 (1).md` §3 (architecture), §5 (gates), §6 (modules),
§10–11 (config + storage), §16 (owner decisions), §19 (kickoff).
Agent rules: `AGENTS.md` PD-1…PD-13.

Written because M0 exceeds the 150-LOC design-note threshold.

## Scope (M0 only)

T0.1 monorepo + uv/pnpm + native PostgreSQL + Windows-native scripts + Makefile + CI gates
T0.2 FastAPI skeleton, `/v1/status`, Alembic baseline, OpenAPI → TS client, error envelope + codes
T0.3 Next.js shell: topbar, nav, design tokens, state components
T0.4 `docs/DECISIONS.md`, `config/defaults.yaml`, append-only result-table pattern + lock triggers

Out of scope (PD-9): market ingestion, detectors, strategy, stats, validation, AI/MCP, any UI page
beyond the shell.

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| MD-1 | Top-level `scripts/` holds the dev entrypoints (`setup.ps1`, `dev*.ps1`, `test.ps1`, `health.ps1`); `infra/docker/` and `infra/migrations/` hold compose files and Alembic | Spec §3 lists both `scripts/{setup.ps1,…}` and `infra/{docker,migrations,scripts}`; §3 gives no other content for either |
| MD-2 | Python 3.12 pinned via `uv python pin 3.12`, not the system interpreter | Machine has `py` 3.13 and a WindowsApps store stub for `python`; uv-managed 3.12 keeps R10 reproducible |
| MD-3 | `make` GNU 4.4.1 installed per-user via scoop (no admin on this machine) | `make check` is a blocking gate (AGENTS §3); choco/winget both require elevation |
| MD-4 | PostgreSQL 18 on `localhost:5432` (already running) hosts a dedicated `cryptoos` role + database; credentials live only in `.env` | Spec §3.1; PD-8 |
| MD-5 | Error envelope is `{code, message, detail, ref_id}` with a frozen `ErrorDetail.code` enum | Spec §12 error envelope |
| MD-6 | Result tables are append-only; only `status`/`locked_at` may update; `ref_id` immutable | PD-13, spec §11 |
| MD-7 | Undocumented parameters are rejected at load time (HTTP 422 at the API edge, `ConfigError` in-process) | PD-11 |

## Layout

```
apps/{web,api,worker}          Next.js / FastAPI / task worker
packages/{schemas,client,ui}   Pydantic+TS schemas, generated TS client, shared UI
engine/{data,features,events,strategy,stats,validation,experiments,ai,mcp}
exchanges/binance              read-only market client (PD-1)
specs/{reference,events,design}+ fixtures/ + config/
data/{raw,normalized,derived}  gitignored, immutable (PD-7)
infra/{docker,migrations}      compose + Alembic
scripts/*.ps1                  Windows-native entrypoints (R10)
tests/                         pytest + e2e
```

## Gates

`make check` = ruff → mypy --strict → pytest → web typecheck → web build.
Everything runs through `scripts/*.ps1` so the same commands work without `make`.

## Open questions carried

- OQ-1 climax-no-trade dot semantics (parity of one plot layer only) — owner.
- OQ-2 `fail_mode` string mapping — owner.
- Six `SPEC_REQUIRED` detectors in `specs/events/` stay unimplemented (PD-4).
