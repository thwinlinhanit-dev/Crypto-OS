"""M6 (T6.1) — the backtest runner: the job-side glue around the pure simulator.

``run_backtest_job`` (called by ``engine.jobs.execute_job`` for BACKTEST
jobs) loads the manifest-backed bars, computes the strategy's detector
signals, runs ``simulate`` per instrument and persists ``strategy.backtest``
+ ``strategy.trade`` rows in the caller's transaction (D-042: the simulator
stays pure; all persistence lives here).

Fail-closed (PD-4/PD-10/PD-12): unknown experiment/strategy-version 404,
unknown dataset_version 422, SHORT_ONLY/BOTH 422 until M10 (R11, D-043),
``entry.mode = RANDOM_BASELINE`` 422 until M7 (7.6, parked).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl
from sqlalchemy import Connection, text

from apps.api.settings import get_settings
from engine.data.parquet import read_part
from engine.errors import NotFoundError, ValidationError
from engine.events.registry import detect_with
from engine.strategy.models import Side, StrategyDefinition
from engine.strategy.simulator import Bar, Signal, simulate
from engine.versions import ENGINE_VERSION, code_version

_FILL_MODELS = ("PARITY", "REALISTIC_OHLC")


def _require(conn: Connection, sql: str, params: dict[str, Any], what: str) -> Any:
    row = conn.execute(text(sql), params).first()
    if row is None:
        raise NotFoundError(f"{what} not found", {k: str(v) for k, v in params.items()})
    return row


def _parse_iso(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _bars_from_frame(frame: pl.DataFrame, exclusions: list[tuple[int, int]]) -> list[Bar]:
    """Closed bars as simulator inputs; excluded intervals flagged (E2/E4)."""
    bars: list[Bar] = []
    for row in frame.iter_rows(named=True):
        open_ms = int(row["open_time"])
        close_ms = int(row["close_time"])
        excluded = any(start <= open_ms < end for start, end in exclusions)
        bars.append(
            Bar(
                index=len(bars),
                time=_iso_ms(close_ms),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                excluded=excluded,
            )
        )
    return bars


def _iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _atr_series(bars: list[Bar], length: int) -> list[float | None]:
    """Wilder ATR over the bar list (None during warm-up, §5 rule)."""
    if len(bars) < length + 1:
        return [None] * len(bars)
    true_ranges: list[float] = []
    for i, bar in enumerate(bars):
        if i == 0:
            true_ranges.append(bar.high - bar.low)
        else:
            prev_close = bars[i - 1].close
            true_ranges.append(
                max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
            )
    atr: list[float | None] = [None] * len(bars)
    seed = sum(true_ranges[:length]) / length
    atr[length] = seed
    previous = seed
    for i in range(length + 1, len(bars)):
        previous = (previous * (length - 1) + true_ranges[i - 1]) / length
        atr[i] = previous
    return atr


def _signals_from_frame(
    strategy: StrategyDefinition,
    frame: pl.DataFrame,
    bars: list[Bar],
    *,
    instrument_id: str,
    timeframe: str,
) -> list[Signal]:
    """Detector signals for the strategy's entry (7.1: the detector is named
    in the strategy, the signals list filters types). Events map back to bar
    indices through their event_time (PD-2, no lookahead)."""
    entry = strategy.entry
    detector_id, _, version = entry.detector.partition("@")
    if detector_id != "RVOL_CLIMAX":
        raise ValidationError(
            "only the RVOL_CLIMAX detector has a runner in M6",
            {"detector": entry.detector},
        )
    events = detect_with(
        detector_id,
        frame,
        version=version or None,
        params=dict(entry.params) or None,
        instrument_id=instrument_id,
        timeframe=timeframe,
    )
    by_time = {bar.time: bar.index for bar in bars}
    wanted = set(entry.signals)
    signals: list[Signal] = []
    for event in events:
        if event.event_type not in wanted:
            continue
        index = by_time.get(event.event_time)
        if index is not None:
            signals.append(
                Signal(bar_index=index, event_type=event.event_type, direction=event.direction)
            )
    return signals


def run_backtest_job(conn: Connection, params: dict[str, Any]) -> UUID:
    """Run one backtest from job params; returns the new backtest_id.

    The job row's transaction is the caller's: the backtest + trades are
    committed together with the SUCCEEDED outcome (engine.jobs pattern).
    """
    backtest_id = UUID(str(params["backtest_id"]))
    experiment_id = UUID(str(params["experiment_id"]))
    strategy_id = str(params["strategy_id"])
    version = str(params["version"])
    fill_model = str(params["fill_model"])
    data_scope = str(params["data_scope"])
    universe = [str(i) for i in params["universe"]]
    timeframe = str(params["timeframe"])
    period_start = _parse_iso(params.get("period_start"))
    period_end = _parse_iso(params.get("period_end"))

    if fill_model not in _FILL_MODELS:
        raise ValidationError("unknown fill_model", {"fill_model": fill_model,
                                                     "allowed": list(_FILL_MODELS)})
    if data_scope not in {"DEVELOPMENT", "HOLDOUT"}:
        raise ValidationError("data_scope must be DEVELOPMENT or HOLDOUT",
                              {"data_scope": data_scope})
    if not universe:
        raise ValidationError("universe is required and must not be empty (PD-12)",
                              {"universe": universe})

    strategy_row = _require(
        conn,
        "SELECT definition FROM strategy.strategy_version WHERE strategy_id = :s AND version = :v",
        {"s": strategy_id, "v": version},
        "strategy version",
    )
    strategy = StrategyDefinition.model_validate(strategy_row.definition)

    if strategy.entry.side in (Side.SHORT_ONLY, Side.BOTH):
        raise ValidationError(
            "short-side strategies are disabled until M10 (R11)",
            {"side": strategy.entry.side.value},
        )
    if strategy.entry.mode != "SIGNAL":
        raise ValidationError(
            "only entry.mode=SIGNAL is implemented; RANDOM_BASELINE lands with M7",
            {"mode": strategy.entry.mode},
        )

    experiment = _require(
        conn,
        "SELECT experiment_id, dataset_version FROM research.experiment WHERE experiment_id = :id",
        {"id": experiment_id},
        "experiment",
    )
    dataset_version = str(params.get("dataset_version") or experiment.dataset_version)
    _require(
        conn,
        "SELECT dataset_version FROM market.dataset_manifest WHERE dataset_version = :dv",
        {"dv": dataset_version},
        "dataset manifest",
    )

    conn.execute(
        text(
            "UPDATE strategy.backtest SET status = 'running',"
            " engine_version = :ev, code_version = :cv"
            " WHERE backtest_id = :id"
        ),
        {
            "id": backtest_id,
            "ev": ENGINE_VERSION,
            "cv": code_version(),
        },
    )

    trades: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    equity_curve: list[tuple[str, float]] = []

    for instrument_id in universe:
        loaded = _load_bars(conn, instrument_id, timeframe, dataset_version, period_start, period_end)
        if loaded is None:
            continue
        frame, bars = loaded
        if not bars:
            continue
        atr = _atr_series(bars, strategy.stop.atr_len)
        signals = _signals_from_frame(
            strategy, frame, bars, instrument_id=instrument_id, timeframe=timeframe
        )
        result = simulate(
            strategy,
            instrument_id,
            bars,
            signals,
            atr=atr,
            structure_level=None,
            tick_size=float(params.get("tick_size", 1e-8)),
            lot_step=float(params.get("lot_step", 1e-8)),
        )
        trades.extend(_trade_rows(backtest_id, result))
        skipped.extend(
            {
                "instrument": instrument_id,
                "signal_bar_index": s.signal_bar_index,
                "signal_time": s.signal_time,
                "event_type": s.event_type,
                "outcome": s.outcome,
                "reason": s.reason,
            }
            for s in result.skipped
        )
        equity_curve.extend(result.equity_curve)

    for trade in trades:
        conn.execute(
            text(
                "INSERT INTO strategy.trade (backtest_id, trade_no, instrument_id, side,"
                " signal_time, decision_time, fill_time, exit_signal_time, exit_decision_time,"
                " exit_fill_time, entry_price, exit_price, qty, fees, pnl, pnl_pct, r_multiple,"
                " mfe_r, mae_r, bars_held, entry_tag, exit_reason, data_gap_during_hold,"
                " straddles_boundary)"
                " VALUES (:bid, :no, :inst, :side, :st, :dt, :ft, :xst, :xdt, :xft, :ep, :xp,"
                " :qty, :fees, :pnl, :pnl_pct, :r, :mfe, :mae, :bars, :tag, :reason, :gap, :straddle)"
            ),
            trade,
        )

    metrics = _metrics(trades, equity_curve)
    conn.execute(
        text(
            "UPDATE strategy.backtest SET status = 'succeeded',"
            " metrics = CAST(:m AS jsonb), skipped = CAST(:s AS jsonb)"
            " WHERE backtest_id = :id"
        ),
        {
            "m": json.dumps(metrics, sort_keys=True),
            "s": json.dumps(skipped, sort_keys=True),
            "id": backtest_id,
        },
    )
    return backtest_id


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _load_bars(
    conn: Connection,
    instrument_id: str,
    timeframe: str,
    dataset_version: str,
    period_start: datetime | None,
    period_end: datetime | None,
) -> tuple[pl.DataFrame, list[Bar]] | None:
    """Manifest-backed closed bars for one instrument (D-013 partial windows).
    Returns (frame, bars) or None when no source file covers the instrument."""
    manifest = conn.execute(
        text("SELECT source_files FROM market.dataset_manifest WHERE dataset_version = :dv"),
        {"dv": dataset_version},
    ).first()
    if manifest is None:
        raise NotFoundError("dataset manifest not found", {"dataset_version": dataset_version})
    data_dir = Path(get_settings().data_dir)
    frames = []
    for source in manifest.source_files or []:
        path = source["path"] if isinstance(source, dict) else source.path
        if instrument_id not in str(path):
            continue
        frames.append(read_part(data_dir, str(path)))
    if not frames:
        return None
    frame = pl.concat(frames).sort("open_time")
    if period_start is not None:
        frame = frame.filter(pl.col("open_time") >= int(period_start.timestamp() * 1000))
    if period_end is not None:
        frame = frame.filter(pl.col("close_time") <= int(period_end.timestamp() * 1000))
    exclusions = [
        (int(g.exclusion_start.timestamp() * 1000), int(g.exclusion_end.timestamp() * 1000))
        for g in conn.execute(
            text(
                "SELECT exclusion_start, exclusion_end FROM system.gap"
                " WHERE instrument_id = :i AND status = 'EXCLUDED'"
            ),
            {"i": instrument_id},
        ).all()
    ]
    return frame, _bars_from_frame(frame, exclusions)


def _trade_rows(backtest_id: UUID, result: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for no, trade in enumerate(result.trades, start=1):
        rows.append(
            {
                "bid": backtest_id,
                "no": no,
                "inst": trade.instrument,
                "side": trade.side,
                "st": _parse_iso(trade.signal_time),
                "dt": _parse_iso(trade.decision_time),
                "ft": _parse_iso(trade.fill_time),
                "xst": _parse_iso(trade.exit_signal_time),
                "xdt": _parse_iso(trade.exit_decision_time),
                "xft": _parse_iso(trade.exit_fill_time),
                "ep": trade.entry_price,
                "xp": trade.exit_price,
                "qty": trade.qty,
                "fees": trade.entry_fee + trade.exit_fee,
                "pnl": trade.pnl,
                "pnl_pct": trade.pnl_pct,
                "r": trade.r_multiple,
                "mfe": trade.mfe_r,
                "mae": trade.mae_r,
                "bars": trade.bars_held,
                "tag": trade.entry_tag,
                "reason": trade.exit_reason,
                "gap": trade.data_gap_during_hold,
                "straddle": trade.straddles_boundary,
            }
        )
    return rows


def _metrics(trades: list[dict[str, Any]], equity_curve: list[tuple[str, float]]) -> dict[str, Any]:
    """Summary metrics block (M6); the full M7 validation suite computes the rest."""
    closed = [t for t in trades if t["reason"] is not None and t["reason"] != "END_OF_DATA"]
    wins = [t for t in closed if (t["pnl"] or 0) > 0]
    total_pnl = sum(t["pnl"] or 0 for t in closed)
    r_values = [t["r"] for t in closed if t["r"] is not None]
    return {
        "trade_count": len(trades),
        "closed_count": len(closed),
        "open_count": len(trades) - len(closed),
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "total_pnl": total_pnl,
        "avg_r": (sum(r_values) / len(r_values)) if r_values else None,
        "final_equity": equity_curve[-1][1] if equity_curve else None,
        "flagged_count": sum(1 for t in trades if t["gap"]),
    }
