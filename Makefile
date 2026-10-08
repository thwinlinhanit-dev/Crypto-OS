# CryptoOS - task entry points.
#
# Recipes must stay shell-agnostic (Windows cmd.exe or POSIX sh) and resolve
# paths with forward slashes. Anything not built yet exits 1 with
# NOT_IMPLEMENTED so a green `make check` can never imply unfinished work (PD-4).

UV ?= uv
PW := powershell -NoProfile -ExecutionPolicy Bypass

.PHONY: help setup check lint typecheck test web-typecheck web-build db-upgrade dev dev-api dev-worker dev-web health ingest seed-fixtures parity

help:
	@echo CryptoOS make targets:
	@echo   setup           bootstrap env (uv sync, .env, migrations, pnpm install)
	@echo   check           lint + typecheck + tests + web typecheck/build (CI gate)
	@echo   lint            ruff check
	@echo   typecheck       mypy (strict)
	@echo   test            pytest
	@echo   web-typecheck   tsc across the pnpm workspace
	@echo   web-build       next build
	@echo   db-upgrade      alembic upgrade head
	@echo   dev / dev-api / dev-worker / dev-web   local processes
	@echo   health          GET /v1/status
	@echo   ingest ARGS="--start ... --end ..."   live Binance backfill (network)
	@echo   seed-fixtures   write the committed fixtures through the ingest path
	@echo   parity          layer-1 parity vs the TradingView export (spec 14.2)

setup:
	$(PW) -File scripts/setup.ps1

lint:
	$(UV) run ruff check .

typecheck:
	$(UV) run mypy .

test:
	$(UV) run pytest

node_modules:
	@echo node_modules is missing - run make setup first.
	@exit 1

web-typecheck: | node_modules
	pnpm -r typecheck

web-build: | node_modules
	pnpm --filter web build

check: lint typecheck test web-typecheck web-build
	@echo make check: PASS

db-upgrade:
	$(UV) run alembic -c infra/migrations/alembic.ini upgrade head

dev:
	$(PW) -File scripts/dev.ps1

dev-api:
	$(PW) -File scripts/dev-api.ps1

dev-worker:
	$(PW) -File scripts/dev-worker.ps1

dev-web:
	$(PW) -File scripts/dev-web.ps1

health:
	$(PW) -File scripts/health.ps1

ingest:
	$(UV) run python scripts/ingest.py $(ARGS)

seed-fixtures:
	$(UV) run python scripts/seed_fixtures.py

parity:
	$(UV) run python scripts/parity.py
