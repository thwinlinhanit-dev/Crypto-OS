"""Idempotent event/outcome persistence (spec 10; migration 0004).

``research.event`` / ``research.event_outcome`` are INSERT-only
(content-addressed facts, PD-7/PD-13): every write is ``ON CONFLICT DO
NOTHING`` on the primary key, so on-demand recompute is idempotent and a
second identical run stores no duplicate rows.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from sqlalchemy import Connection, text

from engine.events.base import Event
from engine.events.outcomes import Outcome


def persist_events(
    conn: Connection, events: Sequence[Event], outcomes: Mapping[str, Outcome]
) -> None:
    """Insert events and all their horizon outcomes; conflicts are ignored."""
    for event in events:
        conn.execute(
            text(
                "INSERT INTO research.event (event_id, detector_id, event_type, version,"
                " params_hash, instrument_id, timeframe, event_time, direction, ref_price,"
                " invalidation_price, regime, features, trigger, lookback_gap,"
                " dataset_version, engine_version) VALUES (:event_id, :detector_id,"
                " :event_type, :version, :params_hash, :instrument_id, :timeframe,"
                " :event_time, :direction, :ref_price, :invalidation_price,"
                " CAST(:regime AS jsonb), CAST(:features AS jsonb), CAST(:trigger AS jsonb),"
                " :lookback_gap, :dataset_version, :engine_version) ON CONFLICT DO NOTHING"
            ),
            {
                "event_id": event.event_id,
                "detector_id": event.detector_id,
                "event_type": event.event_type,
                "version": event.version,
                "params_hash": event.params_hash,
                "instrument_id": str(event.instrument_id),
                "timeframe": event.timeframe,
                "event_time": event.event_time,
                "direction": event.direction,
                "ref_price": float(event.ref_price),
                "invalidation_price": float(event.invalidation_price),
                "regime": json.dumps(dict(event.regime)),
                "features": json.dumps(dict(event.features)),
                "trigger": json.dumps(dict(event.trigger)),
                "lookback_gap": bool(event.lookback_gap),
                "dataset_version": event.dataset_version,
                "engine_version": event.engine_version,
            },
        )
        outcome = outcomes[event.event_id]
        for horizon, row in outcome.horizons.items():
            conn.execute(
                text(
                    "INSERT INTO research.event_outcome (event_id, horizon, fwd_ret_pct,"
                    " fwd_ret_r, mfe_r, mae_r, bars_to_mfe, bars_to_mae, hit_invalidation,"
                    " complete) VALUES (:event_id, :horizon, :fwd_ret_pct, :fwd_ret_r,"
                    " :mfe_r, :mae_r, :bars_to_mfe, :bars_to_mae, :hit_invalidation,"
                    " :complete) ON CONFLICT DO NOTHING"
                ),
                {
                    "event_id": event.event_id,
                    "horizon": int(horizon),
                    "fwd_ret_pct": row.fwd_ret_pct,
                    "fwd_ret_r": row.fwd_ret_r,
                    "mfe_r": row.mfe_r,
                    "mae_r": row.mae_r,
                    "bars_to_mfe": row.bars_to_mfe,
                    "bars_to_mae": row.bars_to_mae,
                    "hit_invalidation": row.hit_invalidation,
                    "complete": bool(row.complete),
                },
            )
