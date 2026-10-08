"""Write the committed fixtures through the live ingest path (design note 9).

Usage: ``uv run python scripts/seed_fixtures.py`` (or ``make seed-fixtures``).
Offline and deterministic: bars come from ``fixtures/binance`` via
``run_seed`` -> ``run_ingest`` -- never from the network (PD-10), and the
dataset_version depends only on the fixture content (PD-6).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.db import connection_scope  # noqa: E402  (path bootstrap above)
from apps.api.settings import get_settings  # noqa: E402
from engine.data.seed import run_seed  # noqa: E402


async def main() -> None:
    data_dir = Path(get_settings().data_dir)
    now_ms = int(time.time() * 1000)
    with connection_scope() as conn:
        result = await run_seed(conn, data_dir=data_dir, now_ms=now_ms, ingested_at_ms=now_ms)
    print(f"dataset_version={result.dataset_version}")
    print(
        f"files_written={result.files_written} files_reused={result.files_reused} "
        f"derived_written={result.derived_written}"
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # script boundary: report, exit non-zero
        print(f"seed-fixtures failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
