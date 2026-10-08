"""Record the committed seed fixtures from Binance REST (design note section 9).

Run once from the repo root, review, commit -- afterwards the files under
``fixtures/binance`` are golden and NEVER re-recorded (section 14.1; a change
needs a PR note). Only bars closed at recording time are written: the forming
bar is excluded per timeframe. Slim rows carry
``[open_time, open, high, low, close, volume, quote_volume, trades]``; the
request parameters, source URL, range and ``recorded_at`` land in the file's
metadata.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402  (path bootstrap above)

from engine.data.bars import BAR_MS, Timeframe, to_iso  # noqa: E402
from engine.data.seed import (  # noqa: E402
    FIXTURES_ROOT,
    SEED_NATIVE_SYMBOLS,
    SEED_TIMEFRAMES,
    write_fixture,
)

KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_INTERVAL: dict[Timeframe, str] = {
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
}
PAGE_LIMIT = 1000
#: Polite gap between pages (public weight budget is 1200/min; we use ~100 requests).
PAGE_SLEEP_SECONDS = 0.05


def fetch_closed_klines(
    client: httpx.Client, *, symbol: str, timeframe: Timeframe, start_ms: int, now_ms: int
) -> list[list[Any]]:
    """Slim venue rows for bars opening in ``[start_ms, last closed open]``."""
    bar_ms = BAR_MS[timeframe]
    last_closed_open = (now_ms // bar_ms) * bar_ms - bar_ms
    rows: list[Any] = []
    cursor = start_ms
    while cursor <= last_closed_open:
        response = client.get(
            KLINES_URL,
            params={
                "symbol": symbol,
                "interval": BINANCE_INTERVAL[timeframe],
                "startTime": cursor,
                "endTime": last_closed_open,
                "limit": PAGE_LIMIT,
            },
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + 1
        if next_cursor <= cursor:
            raise RuntimeError(f"pagination stalled at {cursor} for {symbol} {timeframe.value}")
        cursor = next_cursor
        time.sleep(PAGE_SLEEP_SECONDS)

    slim: list[list[Any]] = []
    seen: set[int] = set()
    for row in rows:
        open_ms = int(row[0])
        if open_ms < start_ms or open_ms > last_closed_open or open_ms in seen:
            continue
        seen.add(open_ms)
        slim.append([open_ms, row[1], row[2], row[3], row[4], row[5], row[7], int(row[8])])
    return slim


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="scripts/record_fixtures.py",
        description="Record fixtures/binance/{SYMBOL}/{TF}.json from Binance REST (once).",
    )
    parser.add_argument(
        "--start",
        default="2024-01-01T00:00:00Z",
        help="first bar to record, ISO-8601 UTC (default: 2024-01-01T00:00:00Z)",
    )
    parser.add_argument(
        "--out",
        default=str(FIXTURES_ROOT),
        help=f"output root (default: {FIXTURES_ROOT.as_posix()})",
    )
    args = parser.parse_args()

    start_ms = _iso_ms(args.start)
    now_ms = int(time.time() * 1000)
    out_root = Path(args.out)
    failures = 0
    with httpx.Client(
        timeout=30.0, headers={"User-Agent": "CryptoOS-fixture-recorder/0.1"}
    ) as client:
        for symbol in SEED_NATIVE_SYMBOLS:
            for timeframe in SEED_TIMEFRAMES:
                label = f"{symbol} {timeframe.value}"
                try:
                    rows = fetch_closed_klines(
                        client, symbol=symbol, timeframe=timeframe, start_ms=start_ms, now_ms=now_ms
                    )
                except httpx.HTTPError as exc:
                    print(f"FAILED {label}: {exc}", file=sys.stderr)
                    failures += 1
                    continue
                if not rows:
                    print(f"FAILED {label}: no bars returned", file=sys.stderr)
                    failures += 1
                    continue
                path = out_root / symbol / f"{timeframe.value}.json"
                write_fixture(
                    path,
                    source=KLINES_URL,
                    request={
                        "symbol": symbol,
                        "interval": BINANCE_INTERVAL[timeframe],
                        "startTime": start_ms,
                        "endTime": (now_ms // BAR_MS[timeframe]) * BAR_MS[timeframe]
                        - BAR_MS[timeframe],
                    },
                    recorded_at_ms=now_ms,
                    rows=rows,
                )
                print(
                    f"{label}: {len(rows)} bars {to_iso(int(rows[0][0]))} .. "
                    f"{to_iso(int(rows[-1][0]))} -> {path.as_posix()}"
                )
    if failures:
        print(
            "partial recording: delete the files written this run and retry "
            "(committed fixtures are never re-recorded)",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _iso_ms(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"--start is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise SystemExit(f"--start must carry a UTC offset (Z): {value!r}")
    return int(parsed.timestamp() * 1000)


if __name__ == "__main__":
    main()
