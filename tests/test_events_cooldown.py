"""M4 (T4.1) - the parity-critical cooldown gate (spec section 6.7).

Golden fixtures A-G under fixtures/events/cooldown/ drive exact comparisons -
no tolerance. The gate implements Pine ``sig = raw and nz(barssince(raw)[1],
999) >= cooldown``; fixture A is the bar-by-bar proof that the ``[1]`` lag
makes a raw bar never suppress *itself* (design note section 3, D-022).

Acceptance evidence produced here:
- every fixture A-G emits exactly the expected boolean series;
- state is independent per event type (fixture F);
- ``cooldown=0`` emits every raw bar (fixture G);
- the first-ever raw always emits (gap seeded to 999, fixture E).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from engine.events.cooldown import apply_cooldown

FIXTURE_DIR = Path("fixtures") / "events" / "cooldown"
FIXTURE_IDS = ("A", "B", "C", "D", "E", "F", "G")


def _load(fixture_id: str) -> dict[str, Any]:
    path = FIXTURE_DIR / f"{fixture_id}.json"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_fixture_golden(fixture_id: str) -> None:
    """Each Section 6.7 fixture emits exactly its ``expected_emit`` series."""
    fixture = _load(fixture_id)
    cooldown = int(fixture["cooldown"])
    for stream in fixture["streams"]:
        raw = [bool(value) for value in stream["raw"]]
        expected = [bool(value) for value in stream["expected_emit"]]
        assert apply_cooldown(raw, cooldown) == expected, (
            f"fixture {fixture_id} stream {stream['name']}: "
            f"got {apply_cooldown(raw, cooldown)} want {expected}"
        )


def test_fixture_a_lag_explained() -> None:
    """Fixture A bar-by-bar: the raw run of three emits once, not three times.

    barssince = [None, 0, 0, 0, 1, 2, 3, 4, 5, 0, 1]
    gap (lag) = [999, 999, 0, 0, 0, 1, 2, 3, 4, 5, 0]
    At bar 1 (first raw) gap=999 >= 5 -> emit. At bars 2-3 gap=0 < 5 -> suppress
    (they see the previous bar's barssince=0). At bar 9 gap=5 -> emit again.
    """
    raw = [False, True, True, True, False, False, False, False, False, True, False]
    assert apply_cooldown(raw, 5) == [
        False,
        True,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        False,
    ]


def test_empty_series() -> None:
    assert apply_cooldown([], 5) == []


def test_cooldown_zero_every_raw_emits() -> None:
    raw = [False, True, True, False, True]
    assert apply_cooldown(raw, 0) == [False, True, True, False, True]


def test_independent_streams_do_not_share_state() -> None:
    """A CONT raw must not suppress a CAPIT raw fired two bars later."""
    cont = apply_cooldown([False, True, False, False, False, False], 5)
    capit = apply_cooldown([False, False, False, True, False, False], 5)
    assert cont == [False, True, False, False, False, False]
    assert capit == [False, False, False, True, False, False]
