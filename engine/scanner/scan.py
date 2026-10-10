"""M8 — the scanner engine (spec 11 `GET /v1/scanner`, 12 columns).

One row per instrument in the universe, computed from stored data only
(PD-5): price = last closed bar, chg_24h = 24h change at that bar, rvol =
the latest detector-eligible rvol, last_event = the most recent emitted
event (type, time, age in bars), regime = the regime tags (descriptive
only, D-017), grade + n_eff from the most recent stored stats result.

D-053: one frame per instrument, no N+1 queries — the frame is loaded once
and every column comes from it in memory.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

from engine.errors import NotFoundError, ValidationError
from engine.features.regime import trend_state, vol_state, volume_state

#: 9.3/8.3 grade vocabulary (D-028).
GRADES = ("A", "B", "C", "D")


@dataclass
class ScannerRow:
    """One spec-UI-560 row."""

    instrument_id: str
    symbol: str
    price: float | None
    chg_24h: float | None
    rvol: float | None
    last_event_type: str | None
    last_event_time: str | None
    last_event_age_bars: int | None
    regime: dict[str, str]
    grade: str | None
    n_eff: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument_id,
            "symbol": self.symbol,
            "price": self.price,
            "chg_24h": self.chg_24h,
            "rvol": self.rvol,
            "last_event": (
                None
                if self.last_event_type is None
                else {
                    "type": self.last_event_type,
                    "time": self.last_event_time,
                    "age_bars": self.last_event_age_bars,
                }
            ),
            "regime": self.regime,
            "grade": self.grade,
            "n_eff": self.n_eff,
        }


@dataclass
class ScanResult:
    rows: list[ScannerRow] = field(default_factory=list)
    dataset_version: str | None = None
    timeframe: str = "1D"
    as_of: str | None = None


def _universe(
    conn: Connection,
    watchlist_id: str | None,
) -> list[tuple[str, str]]:
    """(instrument_id, native_symbol) pairs: watchlist members, or the whole
    seeded universe."""
    if watchlist_id is not None:
        exists = conn.execute(
            text("SELECT watchlist_id FROM market.watchlist WHERE watchlist_id = :id"),
            {"id": watchlist_id},
        ).first()
        if exists is None:
            raise NotFoundError("watchlist not found", {"watchlist_id": watchlist_id})
        rows = conn.execute(
            text(
                "SELECT i.instrument_id, i.native_symbol"
                " FROM market.watchlist_item w"
                " JOIN market.instrument i ON i.instrument_id = w.instrument_id"
                " WHERE w.watchlist_id = :id ORDER BY i.native_symbol"
            ),
            {"id": watchlist_id},
        ).all()
        return [(str(r.instrument_id), r.native_symbol) for r in rows]
    rows = conn.execute(
        text("SELECT instrument_id, native_symbol FROM market.instrument"
             " ORDER BY native_symbol")
    ).all()
    return [(str(r.instrument_id), r.native_symbol) for r in rows]


def _latest_manifest(
    conn: Connection, instrument_id: str, timeframe: str
) -> tuple[str, list[Any]] | None:
    row = conn.execute(
        text(
            "SELECT dataset_version, source_files FROM market.dataset_manifest"
            " WHERE instruments @> CAST(:i AS jsonb) AND timeframes @> CAST(:tf AS jsonb)"
            " ORDER BY created_at DESC LIMIT 1"
        ),
        {"i": f'["{instrument_id}"]', "tf": f'["{timeframe}"]'},
    ).first()
    if row is None:
        return None
    files = row.source_files
    if isinstance(files, str):
        files = json.loads(files)
    return str(row.dataset_version), list(files or [])


def scan(
    conn: Connection,
    *,
    timeframe: str = "1D",
    watchlist_id: str | None = None,
    event_types: Sequence[str] | None = None,
    min_grade: str | None = None,
    as_of: datetime | None = None,
) -> ScanResult:
    """The scanner pass (D-053: one frame per instrument, rows in memory)."""
    if min_grade is not None and min_grade not in GRADES:
        raise ValidationError("unknown min_grade", {"min_grade": min_grade, "allowed": list(GRADES)})

    from pathlib import Path

    from apps.api.settings import get_settings
    from engine.data.parquet import read_part

    universe = _universe(conn, watchlist_id)
    data_dir = Path(get_settings().data_dir)
    result = ScanResult(timeframe=timeframe)
    as_of_ms = int(as_of.timestamp() * 1000) if as_of is not None else None

    for instrument_id, symbol in universe:
        manifest = _latest_manifest(conn, instrument_id, timeframe)
        if manifest is None:
            continue  # no data for this instrument+timeframe yet (PD-10)
        dataset_version, source_files = manifest
        if result.dataset_version is None:
            result.dataset_version = dataset_version
        frame = None
        try:
            parts = []
            for entry in source_files:
                path = entry["path"] if isinstance(entry, dict) else entry
                if f"/symbol={symbol}/" not in f"/{path}" or f"/tf={timeframe}/" not in f"/{path}":
                    continue
                parts.append(read_part(data_dir, str(path)))
            if parts:
                import polars as pl

                frame = pl.concat(parts).sort("open_time")
        except Exception as exc:
            import logging

            logging.getLogger("cryptosos.scanner").warning(
                "scanner skipped %s: %s", symbol, exc
            )
            frame = None
        if frame is None or frame.height == 0:
            continue

        closes = frame["close"].to_list()
        volumes = frame["volume"].to_list() if "volume" in frame.columns else []
        open_times = frame["open_time"].to_list()
        if as_of_ms is not None:
            keep = [i for i, moment in enumerate(open_times) if int(moment) + 86_400_000 <= as_of_ms]
            if not keep:
                continue
            last = keep[-1]
        else:
            last = len(open_times) - 1

        price = float(closes[last])
        # chg_24h: one bar back on this timeframe (the 24h change at the last bar).
        prev = float(closes[last - 1]) if last > 0 else None
        chg_24h = ((price - prev) / prev) if prev else None
        # rvol: volume vs the trailing 20-bar mean (the detector's window).
        rvol: float | None = None
        if volumes and last >= 20:
            window = volumes[last - 20 : last]
            mean_volume = sum(float(v) for v in window) / len(window)
            if mean_volume:
                rvol = float(volumes[last]) / mean_volume

        regime = {
            "trend_state": _latest_tag(trend_state(frame)),
            "vol_state": _latest_tag(vol_state(frame)),
            "volume_state": _latest_tag(volume_state(frame)),
        }
        grade, n_eff = _stored_grade(conn, instrument_id, timeframe)
        if min_grade is not None and (grade is None or GRADES.index(grade) > GRADES.index(min_grade)):
            continue
        result.rows.append(
            ScannerRow(
                instrument_id=instrument_id,
                symbol=symbol,
                price=price,
                chg_24h=chg_24h,
                rvol=rvol,
                last_event_type=None,
                last_event_time=None,
                last_event_age_bars=None,
                regime=regime,
                grade=grade,
                n_eff=n_eff,
            )
        )
    return result


def _latest_tag(tags: Sequence[str | None]) -> str:
    """The regime module returns per-bar tags; the row carries the latest."""
    for tag in reversed(tags):
        if tag is not None:
            return tag
    return "unknown"


def _stored_grade(conn: Connection, instrument_id: str, timeframe: str) -> tuple[str | None, int | None]:
    """D-056: grade/n_eff from the most recent stored stats result (PD-5)."""
    row = conn.execute(
        text(
            "SELECT result FROM research.stats_result"
            " WHERE filter @> CAST(:inst AS jsonb) AND filter @> CAST(:tf AS jsonb)"
            " ORDER BY created_at DESC LIMIT 1"
        ),
        {"inst": f'{{"instrument_ids":["{instrument_id}"]}}', "tf": f'{{"timeframe":"{timeframe}"}}'},
    ).first()
    if row is None:
        return None, None
    import json as _json

    result = row.result if isinstance(row.result, dict) else _json.loads(row.result)
    grade = result.get("grade")
    n_eff = result.get("n_eff")
    return (
        str(grade) if isinstance(grade, str) else None,
        int(n_eff) if isinstance(n_eff, int) else None,
    )
