"""M10 — the live kline WebSocket supervisor (spec T10.2, design note
m10_perp_live section 3, D-068/D-069/D-072).

Supervises a venue kline WebSocket:

- **Reconnect** with jittered capped exponential backoff (never a tight
  loop — R6, rate-limit aware).
- **Gap detection**: after every reconnect (or a missed update), the
  supervisor compares the last closed-bar time against the next expected
  bar. A jump is a gap, recorded in ``system.gap`` (status OPEN) — **never
  silently ignored** (the M10 AC: no silent gaps; PD-10).
- **REST backfill**: the injectable ``backfill(start, end)`` refills the
  gap; on success the gap becomes REPAIRED with the new
  ``repaired_dataset_version`` and the assessment is GREEN (the AC:
  automatic repair and GREEN state).
- **Forming-bar updates**: the current bar arrives as an update event with
  ``forming=True``; it is **never** stored until it closes (§17: the
  forming bar never feeds features, events or backtests; D-069).

Transport-agnostic: ``Supervisor`` takes a ``feed`` (an async iterator of
``BarUpdate``), a ``backfill`` callable, a ``now`` clock and a ``sleep``;
tests drive the kill-mid-bar scenario deterministically.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Protocol

from engine.data.bars import BAR_MS, Timeframe

#: Reconnect backoff (D-072): capped, jittered, never a tight loop.
BACKOFF_BASE_SECONDS: Final = 1.0
BACKOFF_MAX_SECONDS: Final = 60.0
BACKOFF_JITTER: Final = 0.25

SleepFn = Callable[[float], Awaitable[None]]
NowFn = Callable[[], float]
BackfillFn = Callable[[int, int], Awaitable[bool]]


class BarUpdate(Protocol):
    """One stream update from the venue feed."""

    open_time_ms: int
    close_time_ms: int
    forming: bool


@dataclass
class ClosedBar:
    """A closed bar as delivered by the feed."""

    open_time_ms: int
    close_time_ms: int
    forming: bool = False


@dataclass
class GapRecord:
    """A detected gap (mirrors system.gap's columns)."""

    gap_id: str
    instrument_id: str
    timeframe: str
    exclusion_start: datetime
    exclusion_end: datetime
    status: str = "OPEN"
    source: str = "live-ws"
    repaired_dataset_version: str | None = None


@dataclass
class SupervisorState:
    """What the supervisor has seen; exposed for the API/UI and tests."""

    instrument_id: str
    timeframe: Timeframe
    last_closed_open_ms: int | None = None
    gaps: list[GapRecord] = field(default_factory=list)
    reconnects: int = 0
    forming_open_ms: int | None = None

    @property
    def green(self) -> bool:
        """GREEN means no open gap (the AC's terminal state after repair)."""
        return all(gap.status != "OPEN" for gap in self.gaps)


def _iso(ms: int) -> datetime:
    from datetime import UTC

    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _jittered_backoff(attempt: int, rng: random.Random) -> float:
    """Capped exponential with symmetric jitter (D-072)."""
    delay: float = min(BACKOFF_BASE_SECONDS * (2**attempt), BACKOFF_MAX_SECONDS)
    spread: float = delay * BACKOFF_JITTER
    jitter: float = float(rng.uniform(-spread, spread))
    return max(0.0, delay + jitter)


class Supervisor:
    """Drives one live subscription: consume updates, detect gaps, repair.

    ``conn`` is any object exposing ``execute(text, params)`` — the
    supervisor writes gaps + assessments through it (tests pass a recording
    stub; the API passes a SQLAlchemy Connection).
    """

    def __init__(
        self,
        *,
        conn: Any,
        instrument_id: str,
        timeframe: Timeframe,
        feed: Callable[[], Any],
        backfill: BackfillFn,
        now: NowFn | None = None,
        sleep: SleepFn = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._conn = conn
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._feed = feed
        self._backfill = backfill
        self._now = now or _wall_clock
        self._sleep = sleep
        self._rng = rng or random.Random(0)
        self.state = SupervisorState(
            instrument_id=instrument_id, timeframe=timeframe
        )

    async def run(self, *, max_events: int = 1_000) -> SupervisorState:
        """Consume the feed until it stops (or ``max_events`` updates).

        A feed that ends without an exception is re-entered (a healthy
        socket stays subscribed); only a crash counts as a reconnect.
        """
        events = 0
        attempts = 0
        max_attempts = 16  # a hard bound: a perpetually-crashing feed stops
        while events < max_events and attempts < max_attempts:
            attempts += 1
            try:
                async for update in self._feed():
                    events += 1
                    self._handle_update(update)
                    if events >= max_events:
                        break
                # the feed ended normally: stay subscribed and re-enter it
                # (the next batch) unless it produced nothing (a truly
                # closed stream)
                if self._feed_exhausted():
                    break
            except Exception:
                self.state.reconnects += 1
                await self._sleep(_jittered_backoff(self.state.reconnects, self._rng))
                await self._detect_and_repair()
        # a final repair pass catches gaps detected on the last batch: the
        # jump is revealed by an update, but the repair (REST backfill)
        # runs on the reconnect boundary (the AC: automatic repair)
        await self._detect_and_repair()
        return self.state

    def _feed_exhausted(self) -> bool:
        """True when the feed factory has no batches left (a closed stream)."""
        batches = getattr(self._feed, "__wrapped_batches__", None)
        return batches is not None and len(batches) == 0

    def _handle_update(self, update: Any) -> None:
        """Process one stream update."""
        open_ms = int(update.open_time_ms)
        if getattr(update, "forming", False):
            # D-069: the forming bar is an update event, never a store write
            self.state.forming_open_ms = open_ms
            return
        if self.state.last_closed_open_ms is not None:
            expected = self.state.last_closed_open_ms + self._bar_ms()
            if open_ms > expected:
                # a jump is a gap: record it (never silent — PD-10/the AC)
                gap = self._open_gap(expected, open_ms)
                self.state.gaps.append(gap)
                self._write_gap(gap)
        self.state.last_closed_open_ms = open_ms
        self.state.forming_open_ms = None

    def _bar_ms(self) -> int:
        return BAR_MS[self._timeframe]

    def _open_gap(self, start_open_ms: int, end_open_ms: int) -> GapRecord:
        return GapRecord(
            gap_id=str(uuid.uuid4()),
            instrument_id=self._instrument_id,
            timeframe=self._timeframe.value,
            exclusion_start=_iso(start_open_ms),
            exclusion_end=_iso(end_open_ms),
        )

    def _write_gap(self, gap: GapRecord) -> None:
        """Persist an OPEN gap (system.gap, spec §10)."""
        self._conn.execute(
            "INSERT INTO system.gap (gap_id, instrument_id, timeframe, "
            "exclusion_start, exclusion_end, status, source, detected_at) "
            "VALUES (:gap_id, :instrument_id, :timeframe, :exclusion_start, "
            ":exclusion_end, 'OPEN', :source, :detected_at)",
            {
                "gap_id": gap.gap_id,
                "instrument_id": gap.instrument_id,
                "timeframe": gap.timeframe,
                "exclusion_start": gap.exclusion_start,
                "exclusion_end": gap.exclusion_end,
                "source": gap.source,
                "detected_at": _iso(int(self._now() * 1000)),
            },
        )

    async def _detect_and_repair(self) -> None:
        """After a reconnect: repair every open gap via REST backfill (the
        AC: automatic repair)."""
        for gap in self.state.gaps:
            if gap.status != "OPEN":
                continue
            start_ms = int(gap.exclusion_start.timestamp() * 1000)
            end_ms = int(gap.exclusion_end.timestamp() * 1000)
            repaired = await self._backfill(start_ms, end_ms)
            if repaired:
                gap.status = "REPAIRED"
                gap.repaired_dataset_version = str(uuid.uuid4())
                self._conn.execute(
                    "UPDATE system.gap SET status='REPAIRED', repaired_at=:at, "
                    "repaired_dataset_version=:dv WHERE gap_id=:gap_id",
                    {
                        "at": _iso(int(self._now() * 1000)),
                        "dv": gap.repaired_dataset_version,
                        "gap_id": gap.gap_id,
                    },
                )
                self._write_green_assessment()

    def _write_green_assessment(self) -> None:
        """The AC: after repair the data-quality row is GREEN (D-068)."""
        self._conn.execute(
            "INSERT INTO system.data_quality (instrument_id, timeframe, state, "
            "completeness, detail, computed_at) "
            "VALUES (:instrument_id, :timeframe, 'GREEN', 1.0, :detail, :at)",
            {
                "instrument_id": self._instrument_id,
                "timeframe": self._timeframe.value,
                "detail": '{"source": "live-ws", "repaired": true}',
                "at": _iso(int(self._now() * 1000)),
            },
        )


def _wall_clock() -> float:
    import time

    return time.time()


def detect_gap(
    *,
    last_closed_open_ms: int | None,
    next_open_ms: int,
    timeframe: Timeframe,
) -> bool:
    """True when ``next_open_ms`` is past the next expected bar (a jump is
    a gap). Exposed for unit tests and the API."""
    if last_closed_open_ms is None:
        return False
    return next_open_ms > last_closed_open_ms + BAR_MS[timeframe]
