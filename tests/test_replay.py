"""M8 — replay no-future-leak tests (spec M8 AC: "replay leaks no future
data (test)").

With ``as_of`` set to bar k the response contains no bar after k and no
event with a later ``event_time`` (PD-2, the truncation-invariance
spirit).
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from engine.scanner.replay import as_of_ms, replay_bars, replay_events

T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z
DAY = 86_400_000


def _frame(count: int) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "open_time": [T0 + i * DAY for i in range(count)],
            "close_time": [T0 + (i + 1) * DAY for i in range(count)],
            "open": [100.0 + i for i in range(count)],
            "high": [101.0 + i for i in range(count)],
            "low": [99.0 + i for i in range(count)],
            "close": [100.5 + i for i in range(count)],
            "volume": [1000.0 for _ in range(count)],
        }
    )


def test_as_of_ms_none_is_now() -> None:
    assert as_of_ms(None) is None


def test_as_of_ms_converts() -> None:
    moment = datetime(2024, 1, 3, tzinfo=UTC)
    assert as_of_ms(moment) == int(moment.timestamp() * 1000)


def test_replay_bars_stops_at_as_of() -> None:
    """D-055: bars are filtered to close_time <= as_of."""
    frame = _frame(10)
    as_of = T0 + 5 * DAY  # bars 0..4 closed by then (bar 4 closes at T0+5d)
    visible = replay_bars(frame, as_of)
    assert visible.height == 5
    assert visible["close_time"].max() <= as_of


def test_replay_bars_without_as_of_shows_everything() -> None:
    frame = _frame(10)
    assert replay_bars(frame, None).height == 10


def test_replay_bars_never_shows_a_future_bar() -> None:
    """The AC: with as_of at bar k, no bar after k is visible."""
    frame = _frame(20)
    as_of = T0 + 10 * DAY
    visible = replay_bars(frame, as_of)
    assert visible.height == 10
    assert (visible["open_time"] < as_of).all()


def test_replay_events_sql_filters_by_as_of() -> None:
    """PD-2: the as_of filter is in the SQL itself (a mock bypasses it), so
    the event list returned carries only events whose event_time <= as_of.
    The SQL-level assertion lives in tests/test_replay_api.py against the
    real database; here we pin the parameter contract."""
    from unittest.mock import MagicMock

    conn = MagicMock()
    conn.execute.return_value.all.return_value = []
    as_of = int(datetime(2024, 1, 5, tzinfo=UTC).timestamp() * 1000)
    replay_events(conn, instrument_id="i", detector="RVOL_CLIMAX", as_of=as_of)
    call = conn.execute.call_args[0]
    sql = str(call[0])
    params = call[1]
    assert "event_time <=" in sql
    assert params["as_of_ms"] == as_of


def test_replay_events_without_as_of_has_no_cutoff() -> None:
    from unittest.mock import MagicMock

    conn = MagicMock()
    conn.execute.return_value.all.return_value = []
    replay_events(conn, instrument_id="i", detector="RVOL_CLIMAX", as_of=None)
    sql = str(conn.execute.call_args[0][0])
    assert "event_time <=" not in sql


def test_replay_events_carries_the_time_fields() -> None:
    """Spec 12: trades show signal/decision/fill times (the M6 7.3 fields);
    events carry their event_time so the replay can gate on it."""
    from unittest.mock import MagicMock

    conn = MagicMock()
    conn.execute.return_value.all.return_value = [
        MagicMock(event_id="a",
                  event_time=datetime(2024, 1, 1, tzinfo=UTC),
                  event_type="CONT", direction="LONG")
    ]
    visible = replay_events(conn, instrument_id="i", detector="RVOL_CLIMAX", as_of=None)
    assert visible[0]["event_time"] == "2024-01-01T00:00:00Z"
    assert visible[0]["event_type"] == "CONT"
