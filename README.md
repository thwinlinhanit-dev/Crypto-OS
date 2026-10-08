# CryptoOS

TradingView-identical market research platform. Read-only: no order placement exists in v1 (PD-1).

- Operating rules: [AGENTS.md](./AGENTS.md) — PD-1…PD-13 are binding on every change.
- Specification: [`specs/IMPLEMENTATION_SPEC_v1.1.1 (1).md`](./specs/IMPLEMENTATION_SPEC_v1.1.1%20(1).md) (precedence over the Master Spec).
- Reference logic: [`specs/reference/rvol_climax_pro_v5.pine`](./specs/reference/rvol_climax_pro_v5.pine).
- Event definitions: [`specs/events/`](./specs/events/) — `SPEC_REQUIRED` files are not implementable (PD-4).

## Quick start (Windows-native)

```powershell
make setup           # uv sync + .env + alembic upgrade + pnpm install
make check           # ruff -> mypy --strict -> pytest -> tsc -> next build
make dev-api         # FastAPI on http://127.0.0.1:8000
make dev-web         # Next.js on http://127.0.0.1:3000
make health          # GET /v1/status
```

`make setup` also exists as `scripts\setup.ps1`; the other `scripts/*.ps1`
helpers are what the make targets call.

Python 3.12 (pinned by `.python-version`), Node 24 / pnpm 10, GNU Make 4.4.
PostgreSQL: spec default is `localhost:5432`; this machine's dev cluster runs on
`localhost:5433` (see [docs/DECISIONS.md](./docs/DECISIONS.md) D-002) with the
connection string in the gitignored `.env`. Docker is optional (R10).

## Layout

| Path | Contents |
|------|----------|
| `apps/` | `web` (Next.js), `api` (FastAPI), `worker` (tasks) |
| `engine/` | data, features, events, strategy, stats, validation, experiments, ai, mcp |
| `packages/` | `schemas`, generated `client`, shared `ui` |
| `exchanges/` | read-only Binance market client |
| `config/` | `defaults.yaml`, `dataset_scopes.yaml`, `validation.yaml` |
| `specs/` | reference Pine, events, design notes |
| `data/` | raw / normalized / derived bars — local, immutable, gitignored (PD-7) |
| `docs/` | `DECISIONS.md`, `BACKLOG.md`, `CHANGELOG.md` |
