"""M8 — replay (spec 12, design note m8_scanner section 4).

``as_of`` semantics (D-055): every read the replay view makes carries
``as_of``; bars are filtered to ``close_time <= as_of``, events to
``event_time <= as_of``, outcomes/trades to their fill time. The UI shows
no bars/events/outcomes after ``as_of``; events appear when ``event_time``
is reached; trades show ``signal_time``/``decision_time``/``fill_time``
(the M6 7.3 fields).

The no-future-leak property is asserted directly: with ``as_of`` set to
bar k the response contains no bar after k and no event with a later
``event_time`` (PD-2, the truncation-invariance spirit).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text


def as_of_ms(moment: datetime | None) -> int | None:
    """``as_of`` in epoch milliseconds; None means "now" (no filter)."""
    return None if moment is None else int(moment.timestamp() * 1000)


def replay_bars(
    frame: Any,
    as_of: int | None,
) -> Any:
    """Bars visible at ``as_of``: close_time <= as_of (D-008 close bound)."""
    if as_of is None:
        return frame
    return frame.filter(frame["close_time"] <= as_of)


def replay_events(
    conn: Connection,
    *,
    instrument_id: str,
    detector: str,
    as_of: int | None,
) -> list[dict[str, Any]]:
    """Events visible at ``as_of``: event_time <= as_of (PD-2)."""
    params: dict[str, Any] = {"instrument_id": instrument_id, "detector": detector}
    cutoff = ""
    if as_of is not None:
        cutoff = " AND event_time <= to_timestamp(:as_of_ms / 1000.0)"
        params["as_of_ms"] = as_of
    rows = conn.execute(
        text(
            "SELECT event_id, event_time, event_type, direction"
            " FROM research.event"
            " WHERE instrument_id = :instrument_id AND detector_id = :detector"
            + cutoff
            + " ORDER BY event_time"
        ),
        params,
    ).all()
    return [
        {
            "event_id": str(row.event_id),
            "event_time": row.event_time.isoformat().replace("+00:00", "Z"),
            "event_type": row.event_type,
            "direction": row.direction,
        }
        for row in rows
    ]
