"""M10 — the live WebSocket supervisor tests (spec T10.2, design note
m10_perp_live section 3, D-068/D-069/D-072).

The AC: killing the socket mid-bar yields a detected gap, automatic repair
and GREEN state; no silent gaps. Deterministic: the feed, backfill, clock,
sleep and RNG are all injected.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from engine.data.bars import Timeframe
from engine.market.live import (
    BACKOFF_MAX_SECONDS,
    ClosedBar,
    Supervisor,
    _jittered_backoff,
    detect_gap,
)

DAY_MS = 86_400_000
T0 = 1_767_225_600_000  # 2026-01-01T00:00:00Z

INSTRUMENT = "BTCUSDT-PERP"


class RecordingConn:
    """A recording stand-in for the SQLAlchemy Connection: captures the
    inserts/updates the supervisor issues."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((sql, params or {}))
        return None


def _feed(*updates: list[ClosedBar]) -> Any:
    """A feed factory yielding the given update batches (one batch per
    (re)connection)."""
    batches = list(updates)

    async def _feed_fn() -> Any:
        batch = batches.pop(0)
        for update in batch:
            yield update

    _feed_fn.__wrapped_batches__ = list(batches)  # type: ignore[attr-defined]
    return _feed_fn


async def _ok_backfill(start_ms: int, end_ms: int) -> bool:
    """REST backfill always succeeds (the AC's repair path)."""
    _ = (start_ms, end_ms)
    return True


async def _no_sleep(_seconds: float) -> None:
    return None


def _supervisor(
    conn: RecordingConn,
    updates: list[list[ClosedBar]],
    *,
    backfill: Any = _ok_backfill,
    rng: random.Random | None = None,
) -> Supervisor:
    return Supervisor(
        conn=conn,
        instrument_id=INSTRUMENT,
        timeframe=Timeframe.D1,
        feed=_feed(*updates),
        backfill=backfill,
        now=lambda: 1_767_225_600.0,
        sleep=_no_sleep,
        rng=rng or random.Random(0),
    )


@pytest.mark.asyncio
async def test_detect_gap_when_a_bar_is_skipped() -> None:
    assert detect_gap(
        last_closed_open_ms=T0, next_open_ms=T0 + 2 * DAY_MS, timeframe=Timeframe.D1
    )


@pytest.mark.asyncio
async def test_detect_no_gap_on_consecutive_bars() -> None:
    assert not detect_gap(
        last_closed_open_ms=T0, next_open_ms=T0 + DAY_MS, timeframe=Timeframe.D1
    )


@pytest.mark.asyncio
async def test_detect_no_gap_on_the_first_bar() -> None:
    assert not detect_gap(
        last_closed_open_ms=None, next_open_ms=T0 + DAY_MS, timeframe=Timeframe.D1
    )


@pytest.mark.asyncio
async def test_a_consecutive_stream_stays_green_with_no_gaps() -> None:
    conn = RecordingConn()
    sup = _supervisor(
        conn,
        [
            [
                ClosedBar(T0, T0 + DAY_MS),
                ClosedBar(T0 + DAY_MS, T0 + 2 * DAY_MS),
            ]
        ],
    )
    state = await sup.run()
    assert state.last_closed_open_ms == T0 + DAY_MS
    assert state.gaps == []
    assert state.green
    # no gap rows, no assessment writes
    assert conn.calls == []


@pytest.mark.asyncio
async def test_kill_mid_bar_gap_detected_repaired_green() -> None:
    """THE M10 AC: the socket dies mid-bar; on the next bar the gap is
    detected, backfilled and REPAIRED, and the assessment is GREEN."""
    conn = RecordingConn()
    sup = _supervisor(
        conn,
        [
            [ClosedBar(T0, T0 + DAY_MS)],
            # after the kill the feed resumes at bar 5: bars 1..4 are missing
            [ClosedBar(T0 + 5 * DAY_MS, T0 + 6 * DAY_MS)],
        ],
    )
    # the first batch ends normally; simulate the kill by making the feed
    # raise on the second connection
    sup._feed = _raising_then(_feed(
        [ClosedBar(T0, T0 + DAY_MS)],
        [ClosedBar(T0 + 5 * DAY_MS, T0 + 6 * DAY_MS)],
    ))
    state = await sup.run()

    assert state.reconnects == 1
    assert len(state.gaps) == 1
    gap = state.gaps[0]
    assert gap.status == "REPAIRED"
    assert gap.repaired_dataset_version is not None
    assert state.green  # the AC's terminal state

    sqls = [sql for sql, _ in conn.calls]
    assert any("INSERT INTO system.gap" in sql and "OPEN" in sql for sql in sqls)
    assert any("UPDATE system.gap SET status='REPAIRED'" in sql for sql in sqls)
    assert any("INSERT INTO system.data_quality" in sql and "GREEN" in sql for sql in sqls)


def _raising_then(inner: Any, *, kill_after_batches: int = 1) -> Any:
    """Wrap a feed so the first ``kill_after_batches`` connections succeed
    and the NEXT one raises mid-stream (the kill), then later ones
    delegate to ``inner``."""
    calls = {"n": 0}

    async def _fn() -> Any:
        calls["n"] += 1
        batch_index = calls["n"] - 1
        batches: list[list[ClosedBar]] = getattr(inner, "__wrapped_batches__", [])
        if batch_index >= len(batches):
            return
        if batch_index < kill_after_batches:
            for update in batches[batch_index]:
                yield update
            return
        if batch_index == kill_after_batches:
            # deliver the batch's first bar, then die mid-bar
            batch = batches[batch_index]
            if batch:
                yield batch[0]
            raise ConnectionError("socket killed mid-bar")
        for update in batches[batch_index]:
            yield update

    _fn.__wrapped_batches__ = list(  # type: ignore[attr-defined]
        getattr(inner, "__wrapped_batches__", [])
    )
    return _fn


@pytest.mark.asyncio
async def test_a_gap_is_never_silent() -> None:
    """PD-10: a skipped bar always produces a recorded gap row — never a
    silent skip. (The run's final repair pass then backfills it, which is
    the AC's automatic repair; the point here is the gap was detected and
    written.)"""
    conn = RecordingConn()
    sup = _supervisor(
        conn,
        [
            [
                ClosedBar(T0, T0 + DAY_MS),
                ClosedBar(T0 + 3 * DAY_MS, T0 + 4 * DAY_MS),
            ]
        ],
    )
    state = await sup.run()
    assert len(state.gaps) == 1
    # detected + written (the "never silent" property)
    sqls = [sql for sql, _ in conn.calls]
    assert any("INSERT INTO system.gap" in sql for sql in sqls)


@pytest.mark.asyncio
async def test_a_failed_repair_keeps_the_gap_open_and_not_green() -> None:
    """An UNREPAIRABLE backfill leaves the gap OPEN (never silently green)."""
    conn = RecordingConn()

    async def _fail_backfill(start_ms: int, end_ms: int) -> bool:
        return False

    sup = _supervisor(
        conn,
        [
            [ClosedBar(T0, T0 + DAY_MS)],
            [ClosedBar(T0 + 2 * DAY_MS, T0 + 3 * DAY_MS)],
        ],
        backfill=_fail_backfill,
    )
    sup._feed = _raising_then(
        _feed(
            [ClosedBar(T0, T0 + DAY_MS)],
            [ClosedBar(T0 + 2 * DAY_MS, T0 + 3 * DAY_MS)],
        )
    )
    state = await sup.run()
    assert state.gaps[0].status == "OPEN"
    assert not state.green


@pytest.mark.asyncio
async def test_the_forming_bar_is_never_stored() -> None:
    """D-069 / spec §17: the forming bar is an update event, never a store
    write; the last closed bar is unchanged while a bar forms."""
    conn = RecordingConn()
    sup = _supervisor(
        conn,
        [
            [
                ClosedBar(T0, T0 + DAY_MS),
                ClosedBar(T0 + DAY_MS, T0 + 2 * DAY_MS, forming=True),
            ]
        ],
    )
    state = await sup.run()
    assert state.last_closed_open_ms == T0
    assert state.forming_open_ms == T0 + DAY_MS
    assert state.green


@pytest.mark.asyncio
async def test_backoff_is_capped_and_jittered() -> None:
    """D-072: capped exponential with jitter; never a tight loop."""
    rng = random.Random(7)
    first = _jittered_backoff(0, rng)
    tenth = _jittered_backoff(10, rng)
    assert 0.75 <= first <= 1.25  # 1s +/- 25%
    assert tenth <= BACKOFF_MAX_SECONDS * 1.25  # capped
    assert _jittered_backoff(100, rng) <= BACKOFF_MAX_SECONDS * 1.25


@pytest.mark.asyncio
async def test_reconnect_count_increments_on_each_kill() -> None:
    conn = RecordingConn()
    sup = _supervisor(
        conn,
        [
            [ClosedBar(T0, T0 + DAY_MS)],
            [ClosedBar(T0 + DAY_MS, T0 + 2 * DAY_MS)],
            [ClosedBar(T0 + 2 * DAY_MS, T0 + 3 * DAY_MS)],
        ],
    )
    sup._feed = _raising_then(
        _feed(
            [ClosedBar(T0, T0 + DAY_MS)],
            [ClosedBar(T0 + DAY_MS, T0 + 2 * DAY_MS)],
            [ClosedBar(T0 + 2 * DAY_MS, T0 + 3 * DAY_MS)],
        )
    )
    state = await sup.run()
    assert state.reconnects == 1
    assert state.gaps == []
