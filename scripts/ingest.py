"""Live Binance backfill through ``run_ingest`` (network; design note 9).

Usage: ``uv run python scripts/ingest.py --start 2024-01-01T00:00:00Z --end 2026-10-08T00:00:00Z``
(or ``make ingest ARGS="--start ... --end ..."``).

PD-11: the window has no hidden default -- both ends come from the request
(the command line) or would have to come from ``config/defaults.yaml``; the
storage budget does come from there. Fail-closed: PERPETUAL and non-BINANCE
markets are refused before any fetch.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.db import connection_scope  # noqa: E402  (path bootstrap above)
from apps.api.settings import get_settings  # noqa: E402
from engine.config.defaults import load_defaults  # noqa: E402
from engine.data.ingest import GB, IngestScope, run_ingest  # noqa: E402
from engine.data.instruments import MarketType, upsert_instruments  # noqa: E402
from engine.data.seed import (  # noqa: E402
    SEED_NATIVE_SYMBOLS,
    SEED_TIMEFRAMES,
    seed_instruments,
)
from exchanges.binance.adapter import BinanceAdapter  # noqa: E402


def _iso_ms(value: str, flag: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"{flag} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise SystemExit(f"{flag} must carry a UTC offset (Z): {value!r}")
    return int(parsed.timestamp() * 1000)


async def main() -> None:
    parser = argparse.ArgumentParser(
        prog="scripts/ingest.py",
        description="Live Binance backfill of the seed universe (network).",
    )
    parser.add_argument("--start", required=True, help="window start, ISO-8601 UTC (Z)")
    parser.add_argument("--end", required=True, help="window end, ISO-8601 UTC (Z)")
    args = parser.parse_args()

    start_ms = _iso_ms(args.start, "--start")
    end_ms = _iso_ms(args.end, "--end")
    if end_ms < start_ms:
        raise SystemExit("--end is before --start")

    now_ms = int(time.time() * 1000)
    budget_gb = load_defaults()["data"]["storage_budget_gb"]
    instruments = seed_instruments()
    scope = IngestScope(
        venue="BINANCE",
        market_type=MarketType.SPOT,
        instrument_ids=tuple(instrument.instrument_id for instrument in instruments),
        timeframes=SEED_TIMEFRAMES,
        start_ms=start_ms,
        end_ms=end_ms,
        storage_budget_bytes=int(budget_gb * GB),
    )
    print(
        f"ingesting {len(SEED_NATIVE_SYMBOLS)} symbols x "
        f"{len(SEED_TIMEFRAMES)} timeframes from BINANCE spot: "
        f"{args.start} .. {args.end} (budget {budget_gb} GB)"
    )
    adapter = BinanceAdapter(market_type=MarketType.SPOT)
    try:
        with connection_scope() as conn:
            upsert_instruments(conn, instruments)
            result = await run_ingest(
                conn,
                scope,
                adapter,
                data_dir=Path(get_settings().data_dir),
                now_ms=now_ms,
                ingested_at_ms=now_ms,
            )
    finally:
        await adapter.aclose()
    print(f"dataset_version={result.dataset_version}")
    print(
        f"files_written={result.files_written} files_reused={result.files_reused} "
        f"derived_written={result.derived_written}"
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception as exc:  # script boundary: report, exit non-zero
        print(f"ingest failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
