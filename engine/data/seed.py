"""Seed fixtures: committed venue bars written through the live ingest path.

Design note section 9: ``fixtures/binance/{SYMBOL}/{TF}.json`` holds Binance
klines recorded once by ``scripts/record_fixtures.py`` (section 14.1 golden
fixtures -- never re-recorded once committed). ``run_seed`` loads those files
and calls the same ``run_ingest`` the live path uses: one writer, one
manifest, one Guardian pass, no parallel code path.

INTERPRETATIONS (DECISIONS):
- fixture rows are slim ``[open_time, open, high, low, close, volume,
  quote_volume, trades]`` -- the venue fields the engine consumes; the rest of
  the kline payload is dropped at recording time and the request parameters
  are preserved in the file's metadata;
- the recorded window starts 2024-01-01T00:00:00Z (owner-approved) and ends
  at the last bar closed at recording time;
- a fixture file the seed universe expects but that is missing raises
  ``DATA_UNAVAILABLE`` instead of falling back to a live fetch (PD-10).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from sqlalchemy import Connection

from engine.config.defaults import load_defaults
from engine.data.bars import Timeframe, close_time_ms, to_iso
from engine.data.ingest import GB, IngestResult, IngestScope, run_ingest
from engine.data.instruments import (
    Instrument,
    MarketType,
    build_instrument,
    upsert_instruments,
)
from engine.errors import DataUnavailableError, ValidationError
from exchanges.base import ExchangeAdapter, Kline

#: OD-2 seed universe (native symbols of BTC/ETH/SOL spot on Binance).
SEED_NATIVE_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
#: T1.4 recorded timeframes; ``run_ingest`` derives 1W from 1D automatically.
SEED_TIMEFRAMES: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4, Timeframe.H1)

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "binance"

_FIXTURE_KEYS = ("source", "request", "recorded_at", "range", "row_count", "rows")
_ROW_LEN = 8
#: One fixture row: open_time, open, high, low, close, volume, quote_volume, trades.
SEED_ASSETS: dict[str, str] = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL"}


def fixture_path(root: Path, native_symbol: str, timeframe: Timeframe) -> Path:
    return root / native_symbol / f"{timeframe.value}.json"


def write_fixture(
    path: Path,
    *,
    source: str,
    request: dict[str, Any],
    recorded_at_ms: int,
    rows: list[list[Any]],
) -> None:
    """Write one fixture file (the recorder's writer; loaders validate it back)."""
    if not rows:
        raise ValidationError("cannot record a fixture with no rows", {"path": str(path)})
    payload: dict[str, Any] = {
        "source": source,
        "request": request,
        "recorded_at": to_iso(recorded_at_ms),
        "range": {"start_ms": int(rows[0][0]), "end_ms": int(rows[-1][0])},
        "row_count": len(rows),
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


@dataclass(frozen=True, slots=True)
class FixtureFile:
    native_symbol: str
    timeframe: Timeframe
    range_start_ms: int
    range_end_ms: int
    klines: tuple[Kline, ...]


def load_fixture(root: Path, native_symbol: str, timeframe: Timeframe) -> FixtureFile:
    """Read one fixture file, fail closed on anything malformed (PD-10)."""
    path = fixture_path(root, native_symbol, timeframe)
    if not path.is_file():
        raise DataUnavailableError(
            "no recorded fixture for this market",
            {"path": path.as_posix(), "native_symbol": native_symbol, "timeframe": timeframe.value},
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataUnavailableError("fixture file is unreadable", {"path": path.as_posix()}) from exc
    missing = [key for key in _FIXTURE_KEYS if key not in raw]
    if missing:
        raise ValidationError(
            "fixture envelope is malformed", {"path": path.as_posix(), "missing": missing}
        )
    if raw["row_count"] != len(raw["rows"]):
        raise ValidationError(
            "fixture row_count does not match its rows",
            {"path": path.as_posix(), "row_count": raw["row_count"]},
        )
    klines: list[Kline] = []
    for row in raw["rows"]:
        if len(row) != _ROW_LEN:
            raise ValidationError("fixture row is malformed", {"path": path.as_posix(), "row": row})
        open_ms = int(row[0])
        klines.append(
            Kline(
                open_time=open_ms,
                close_time=close_time_ms(open_ms, timeframe),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                quote_volume=float(row[6]),
                trades=int(row[7]),
            )
        )
    # Golden files must be strictly ordered; a duplicate would poison the writer (PD-7).
    if any(later.open_time <= earlier.open_time for earlier, later in pairwise(klines)):
        raise ValidationError("fixture rows are not strictly increasing", {"path": path.as_posix()})
    return FixtureFile(
        native_symbol=native_symbol,
        timeframe=timeframe,
        range_start_ms=int(raw["range"]["start_ms"]),
        range_end_ms=int(raw["range"]["end_ms"]),
        klines=tuple(klines),
    )


class FixtureKlineAdapter:
    """Offline venue: serves the committed fixture bars, never the network."""

    name = "binance-fixtures"
    market_type = MarketType.SPOT

    def __init__(self, root: Path = FIXTURES_ROOT) -> None:
        self._root = root
        self._files: dict[tuple[str, Timeframe], FixtureFile] = {
            (symbol, timeframe): load_fixture(root, symbol, timeframe)
            for symbol in SEED_NATIVE_SYMBOLS
            for timeframe in SEED_TIMEFRAMES
        }
        self.fetch_calls: list[tuple[str, Timeframe, int, int]] = []

    async def fetch_historical_klines(
        self, *, native_symbol: str, timeframe: Timeframe, start_ms: int, end_ms: int
    ) -> list[Kline]:
        self.fetch_calls.append((native_symbol, timeframe, start_ms, end_ms))
        fixture = self._files.get((native_symbol, timeframe))
        if fixture is None:
            raise DataUnavailableError(
                "no recorded fixture for this market",
                {"native_symbol": native_symbol, "timeframe": timeframe.value},
            )
        return [kline for kline in fixture.klines if start_ms <= kline.open_time <= end_ms]

    async def fetch_instrument_metadata(
        self, *, native_symbols: Sequence[str]
    ) -> list[dict[str, Any]]:
        return []

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "source": "fixtures", "root": self._root.as_posix()}

    async def aclose(self) -> None:
        return None


def seed_instruments() -> list[Instrument]:
    """OD-2 seed universe; uuid5 ids make re-seeding idempotent (PD-6)."""
    return [
        build_instrument(
            venue="BINANCE",
            market_type=MarketType.SPOT,
            asset=asset,
            quote="USDT",
            native_symbol=symbol,
        )
        for symbol, asset in SEED_ASSETS.items()
    ]


async def run_seed(
    conn: Connection,
    *,
    data_dir: Path,
    now_ms: int,
    ingested_at_ms: int,
    fixture_root: Path = FIXTURES_ROOT,
    adapter: ExchangeAdapter | None = None,
) -> IngestResult:
    """Seed BTC/ETH/SOL x 1D/4H/1H (plus derived 1W) through ``run_ingest``."""
    if not fixture_root.is_dir():
        raise DataUnavailableError(
            "no fixtures recorded yet",
            {"root": fixture_root.as_posix(), "record": "uv run python scripts/record_fixtures.py"},
        )
    instruments = seed_instruments()
    upsert_instruments(conn, instruments)
    files = [
        load_fixture(fixture_root, symbol, timeframe)
        for symbol in SEED_NATIVE_SYMBOLS
        for timeframe in SEED_TIMEFRAMES
    ]
    budget_gb = load_defaults()["data"]["storage_budget_gb"]
    scope = IngestScope(
        venue="BINANCE",
        market_type=MarketType.SPOT,
        instrument_ids=tuple(instrument.instrument_id for instrument in instruments),
        timeframes=SEED_TIMEFRAMES,
        start_ms=min(fixture.range_start_ms for fixture in files),
        end_ms=max(fixture.range_end_ms for fixture in files),
        storage_budget_bytes=int(budget_gb * GB),
    )
    return await run_ingest(
        conn,
        scope,
        adapter if adapter is not None else FixtureKlineAdapter(fixture_root),
        data_dir=data_dir,
        now_ms=now_ms,
        ingested_at_ms=ingested_at_ms,
    )


__all__ = [
    "FIXTURES_ROOT",
    "SEED_ASSETS",
    "SEED_NATIVE_SYMBOLS",
    "SEED_TIMEFRAMES",
    "FixtureFile",
    "FixtureKlineAdapter",
    "fixture_path",
    "load_fixture",
    "run_seed",
    "seed_instruments",
    "write_fixture",
]
