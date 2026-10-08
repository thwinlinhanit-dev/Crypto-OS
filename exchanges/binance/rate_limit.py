"""Rolling request-weight budget in front of venue calls (section 4.5).

Weights and the default budget are Binance's published public-REST numbers
(design note 5.1, ASSUMPTION/OQ): they are plumbing facts, not research
parameters, so they are pinned constants here rather than config.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from exchanges.base import SleepFn


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """Request-weight budget for one rolling window (Binance public REST)."""

    budget: int = 6000
    window_seconds: float = 60.0
    klines_weight: int = 2
    exchange_info_weight: int = 20
    time_weight: int = 1


class WeightWindowLimiter:
    """Sleeps until ``weight`` fits the rolling window, then books it.

    Deterministic under an injected clock and sleep: tests advance time by
    recording the sleeps instead of waiting.
    """

    def __init__(
        self,
        *,
        budget: int,
        window_seconds: float,
        now: Callable[[], float] = time.monotonic,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        if budget <= 0:
            raise ValueError("rate-limit budget must be positive")
        self._budget = budget
        self._window_seconds = window_seconds
        self._now = now
        self._sleep = sleep
        self._entries: list[tuple[float, int]] = []

    async def acquire(self, weight: int) -> None:
        if weight > self._budget:
            raise ValueError(
                f"request weight {weight} exceeds the whole budget {self._budget}"
            )
        while True:
            cutoff = self._now() - self._window_seconds
            self._entries = [entry for entry in self._entries if entry[0] > cutoff]
            used = sum(entry_weight for _, entry_weight in self._entries)
            if used + weight <= self._budget:
                self._entries.append((self._now(), weight))
                return
            oldest_time = self._entries[0][0]
            wait = max(oldest_time + self._window_seconds - self._now(), 0.001)
            await self._sleep(wait)
