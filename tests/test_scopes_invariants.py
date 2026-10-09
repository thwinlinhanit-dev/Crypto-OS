"""T5.1 — data-scope invariants (spec section 8.1; M5 AC "scope invariants property tests pass").

The four testable rules of 8.1:

1. the frozen boundary dates in ``config/dataset_scopes.yaml`` are derived
   from their OWN frozen S range exactly as the spec defines them
   (``holdout_start = S_start + 0.80 x span``, ``oos_start = S_start +
   0.56 x span``, each floored to the containing UTC calendar day) and match
   the fixture history the freeze was computed from - a freeze that "moves"
   is a bug (PD-7);
2. the freeze writer refuses to overwrite an existing family entry
   ("boundaries never move after freeze"; a new universe is a NEW entry);
3. DEVELOPMENT windows are clipped to ``[s_start, holdout_start)`` and
   flagged ``SCOPE_CLIPPED`` when they reach past a scope boundary - the
   window shrinks, it never silently becomes another window (PD-10);
4. embargo/purge follow the exact order of 8.1: embargo removes the LATER
   segment's first E bars as origins, purge removes EARLIER-segment origins
   whose H-bar outcome window would cross a boundary, survivors land in
   exactly one segment, and neither step moves a boundary.
"""

from __future__ import annotations

import json
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.data.bars import BAR_MS, parse_timeframe
from engine.errors import ImmutableResultError, ValidationError
from engine.scopes import (
    DAY_MS,
    SCOPE_FILE,
    ScopeBoundaries,
    boundaries_payload,
    clip_window,
    compute_boundaries,
    embargo_purge,
    floor_utc_day,
    freeze_scope,
    load_boundaries,
    segment_of,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SCOPE = REPO_ROOT / "config" / "dataset_scopes.yaml"
FIXTURE_DIR = REPO_ROOT / "fixtures" / "binance"
FAMILY = "binance_spot_usdt_default"

TEST_START = 1_704_067_200_000  # 2024-01-01T00:00:00Z (Monday)
TEST_SPAN = 1_000 * DAY_MS
BAR_1H = 3_600_000
H_BARS = 50
E_BARS = 50
H_MS = H_BARS * BAR_1H

SEGMENT_ORDER = {"IS": 0, "OOS": 1, "HOLDOUT": 2}


def _scope() -> ScopeBoundaries:
    """Synthetic 1000-day scope: boundaries land exactly on UTC midnight."""
    oos_raw, holdout_raw = compute_boundaries(TEST_START, TEST_START + TEST_SPAN)
    return ScopeBoundaries(
        family="test_scope",
        universe=("BINANCE:BTCUSDT",),
        timeframes=("1H",),
        s_start=TEST_START,
        s_end=TEST_START + TEST_SPAN,
        oos_start=floor_utc_day(oos_raw),
        holdout_start=floor_utc_day(holdout_raw),
        span_days=1000,
        frozen_at="2026-10-09T00:00:00Z",
        frozen_by="tests",
    )


def _fixture_history() -> tuple[int, int]:
    """(earliest first-bar open_time, latest closed bar) over the whole fixture grid."""
    first_open: int | None = None
    last_close: int | None = None
    for path in sorted(FIXTURE_DIR.glob("*/*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        bar_ms = BAR_MS[parse_timeframe(path.stem)]
        opens = [row[0] for row in payload["rows"]]
        assert opens, f"empty fixture {path}"
        first_open = opens[0] if first_open is None else min(first_open, opens[0])
        close = opens[-1] + bar_ms
        last_close = close if last_close is None else max(last_close, close)
    assert first_open is not None and last_close is not None
    return first_open, last_close


def _window() -> st.SearchStrategy[tuple[int, int]]:
    edge = st.integers(
        min_value=TEST_START - 5 * DAY_MS,
        max_value=TEST_START + TEST_SPAN + 5 * DAY_MS,
    )
    return st.tuples(edge, edge).map(lambda w: (min(w), max(w)))


# --- 1. the frozen production entry ------------------------------------------------


def test_frozen_boundaries_are_derived_from_their_own_span() -> None:
    b = load_boundaries(FAMILY, path=PRODUCTION_SCOPE)
    oos_raw, holdout_raw = compute_boundaries(b.s_start, b.s_end)
    assert b.oos_start == floor_utc_day(oos_raw)
    assert b.holdout_start == floor_utc_day(holdout_raw)
    # flooring moves a boundary earlier (never past it) by less than one day
    assert b.oos_start <= oos_raw < b.oos_start + DAY_MS
    assert b.holdout_start <= holdout_raw < b.holdout_start + DAY_MS
    assert holdout_raw == b.s_start + ((b.s_end - b.s_start) * 80) // 100
    assert oos_raw == b.s_start + ((b.s_end - b.s_start) * 56) // 100


def test_frozen_boundary_order_and_floor_slack() -> None:
    b = load_boundaries(FAMILY, path=PRODUCTION_SCOPE)
    assert b.s_start < b.oos_start < b.holdout_start < b.s_end
    span = b.s_end - b.s_start
    _, holdout_raw = compute_boundaries(b.s_start, b.s_end)
    oos_raw, _ = compute_boundaries(b.s_start, b.s_end)
    assert b.holdout_start <= holdout_raw < b.holdout_start + DAY_MS
    assert b.oos_start <= oos_raw < b.oos_start + DAY_MS
    # the holdout still covers the latest ~20% of the calendar span
    assert b.s_end - b.holdout_start >= int(0.20 * span) - DAY_MS
    # the frozen file carries its provenance
    assert b.frozen_at and b.frozen_by


def test_frozen_boundaries_match_the_fixture_history_they_were_computed_from() -> None:
    first_open, last_close = _fixture_history()
    b = load_boundaries(FAMILY, path=PRODUCTION_SCOPE)
    assert (b.s_start, b.s_end) == (first_open, last_close)
    oos_raw, holdout_raw = compute_boundaries(first_open, last_close)
    assert b.oos_start == floor_utc_day(oos_raw)
    assert b.holdout_start == floor_utc_day(holdout_raw)
    # the frozen calendar dates themselves (spec 8.1, logged in DECISIONS)
    payload = boundaries_payload(b, "DEVELOPMENT")
    assert payload["oos_start"] == "2025-07-20T00:00:00Z"
    assert payload["holdout_start"] == "2026-03-19T00:00:00Z"


def test_frozen_universe_is_the_od2_default() -> None:
    b = load_boundaries(FAMILY, path=PRODUCTION_SCOPE)
    assert b.universe == ("BINANCE:BTCUSDT", "BINANCE:ETHUSDT", "BINANCE:SOLUSDT")
    assert b.timeframes == ("1D", "4H", "1H")


def test_unknown_family_fails_closed() -> None:
    with pytest.raises(ValidationError) as exc:
        load_boundaries("no_such_family", path=PRODUCTION_SCOPE)
    assert "no_such_family" in str(exc.value.detail)


def test_missing_file_fails_closed() -> None:
    with pytest.raises(ValidationError):
        load_boundaries(FAMILY, path=Path("config") / "does_not_exist.yaml")


# --- 2. the freeze writer ----------------------------------------------------------


def test_freeze_scope_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "scopes.yaml"
    b = _scope()
    freeze_scope(b, path=path)
    loaded = load_boundaries(b.family, path=path)
    assert loaded == b
    default = load_boundaries(path=path)
    assert default == b


def test_freeze_scope_refuses_to_overwrite_an_existing_family(tmp_path: Path) -> None:
    path = tmp_path / "scopes.yaml"
    freeze_scope(_scope(), path=path)
    with pytest.raises(ImmutableResultError):
        freeze_scope(_scope(), path=path)


# --- 3. window clipping ------------------------------------------------------------


def test_clip_development_is_bounded_by_the_frozen_endpoints() -> None:
    b = _scope()
    # ends exactly at holdout_start: half-open window, not into the holdout
    win = clip_window(TEST_START, b.holdout_start, boundaries=b, scope="DEVELOPMENT")
    assert not win.clipped
    assert win.flag is None
    # one millisecond into the holdout: clipped and flagged
    win = clip_window(TEST_START, b.holdout_start + 1, boundaries=b, scope="DEVELOPMENT")
    assert win.clipped and win.flag == "SCOPE_CLIPPED"
    assert win.applied_end == b.holdout_start
    # starting before the frozen data start: clipped to s_start
    win = clip_window(b.s_start - DAY_MS, b.oos_start, boundaries=b, scope="DEVELOPMENT")
    assert win.clipped and win.flag == "SCOPE_CLIPPED"
    assert win.applied_start == b.s_start


def test_clip_holdout_never_starts_before_the_holdout_boundary() -> None:
    b = _scope()
    win = clip_window(b.s_start, b.s_end, boundaries=b, scope="HOLDOUT")
    assert win.clipped and win.flag == "SCOPE_CLIPPED"
    assert win.applied_start == b.holdout_start
    inside = clip_window(b.holdout_start + 1, b.s_end, boundaries=b, scope="HOLDOUT")
    assert not inside.clipped


def test_clip_rejects_an_unknown_scope() -> None:
    b = _scope()
    with pytest.raises(ValidationError):
        clip_window(TEST_START, TEST_START + DAY_MS, boundaries=b, scope="PRODUCTION")


def test_clip_rejects_an_inverted_window() -> None:
    b = _scope()
    with pytest.raises(ValidationError):
        clip_window(TEST_START + DAY_MS, TEST_START, boundaries=b, scope="DEVELOPMENT")


@settings(max_examples=80, deadline=None)
@given(_window())
def test_clip_development_never_reaches_the_holdout(window: tuple[int, int]) -> None:
    b = _scope()
    start, end = window
    if start >= end or end <= b.s_start or start >= b.holdout_start:
        # outside the scope entirely: fail-closed, never an empty/substitute window
        with pytest.raises(ValidationError):
            clip_window(start, end, boundaries=b, scope="DEVELOPMENT")
        return
    win = clip_window(start, end, boundaries=b, scope="DEVELOPMENT")
    assert b.s_start <= win.applied_start <= win.applied_end <= b.holdout_start
    assert win.clipped == (start != win.applied_start or end != win.applied_end)
    if start < b.holdout_start < end:
        assert win.clipped and win.flag == "SCOPE_CLIPPED"
    # idempotent: clipping an already-clipped window changes nothing
    again = clip_window(win.applied_start, win.applied_end, boundaries=b, scope="DEVELOPMENT")
    assert not again.clipped
    assert (again.applied_start, again.applied_end) == (win.applied_start, win.applied_end)


@settings(max_examples=80, deadline=None)
@given(_window())
def test_clip_never_moves_a_boundary(window: tuple[int, int]) -> None:
    b = _scope()
    before = (b.s_start, b.s_end, b.oos_start, b.holdout_start)
    with suppress(ValidationError):
        clip_window(*window, boundaries=b, scope="DEVELOPMENT")
    with suppress(ValidationError):
        clip_window(*window, boundaries=b, scope="HOLDOUT")
    assert (b.s_start, b.s_end, b.oos_start, b.holdout_start) == before


# --- 4. embargo / purge ------------------------------------------------------------


def test_embargo_removes_the_later_segments_first_e_bars() -> None:
    b = _scope()
    for boundary in (b.oos_start, b.holdout_start):
        origins = [boundary, boundary + E_BARS * BAR_1H - 1]
        report = embargo_purge(
            origins, boundaries=b, horizon_h=H_BARS, embargo_e=E_BARS, bar_ms=BAR_1H
        )
        assert list(report.embargoed) == origins
        assert report.kept == ()
        assert report.purged == ()
        # the first bar AFTER the embargo window survives, in the later segment
        later = boundary + E_BARS * BAR_1H
        ok = embargo_purge(
            [later], boundaries=b, horizon_h=H_BARS, embargo_e=E_BARS, bar_ms=BAR_1H
        )
        assert ok.embargoed == () and ok.purged == ()
        assert len(ok.kept) == 1
        assert ok.kept[0].segment == segment_of(later, b)


def test_purge_removes_earlier_origins_whose_outcome_window_crosses_a_boundary() -> None:
    b = _scope()
    for boundary in (b.oos_start, b.holdout_start):
        # 1 ms before the boundary: the H-bar window certainly crosses it
        crossed = boundary - 1
        report = embargo_purge(
            [crossed], boundaries=b, horizon_h=H_BARS, embargo_e=E_BARS, bar_ms=BAR_1H
        )
        assert list(report.purged) == [crossed]
        assert report.embargoed == () and report.kept == ()
        # exactly H bars before the boundary: t + H == b does NOT cross (spec: t + H > b)
        edge = boundary - H_MS
        edge_report = embargo_purge(
            [edge], boundaries=b, horizon_h=H_BARS, embargo_e=E_BARS, bar_ms=BAR_1H
        )
        assert edge_report.purged == () and edge_report.embargoed == ()
        assert len(edge_report.kept) == 1
        assert edge_report.kept[0].segment == segment_of(edge, b)


def test_oos_boundary_purges_the_is_side_but_not_the_oos_side() -> None:
    b = _scope()
    crossing = b.oos_start - H_MS + 1  # window reaches 1 ms past oos_start -> purge
    before_window = b.oos_start - H_MS - 1  # window ends 1 ms short of oos_start -> keep
    report = embargo_purge(
        [crossing, before_window, b.oos_start + E_BARS * BAR_1H],
        boundaries=b,
        horizon_h=H_BARS,
        embargo_e=E_BARS,
        bar_ms=BAR_1H,
    )
    assert list(report.purged) == [crossing]
    assert report.embargoed == ()
    kept = {a.origin_ms: a.segment for a in report.kept}
    assert kept == {
        before_window: "IS",
        b.oos_start + E_BARS * BAR_1H: "OOS",
    }


def test_segment_assignment_matches_segment_of_for_every_survivor() -> None:
    b = _scope()
    origins = [
        b.s_start,
        b.oos_start - H_MS,
        b.oos_start + E_BARS * BAR_1H,
        b.holdout_start - H_MS,
        b.holdout_start + E_BARS * BAR_1H,
        b.s_end - 1,
    ]
    report = embargo_purge(
        origins, boundaries=b, horizon_h=H_BARS, embargo_e=E_BARS, bar_ms=BAR_1H
    )
    for kept in report.kept:
        assert kept.segment == segment_of(kept.origin_ms, b)
    segments = [a.segment for a in report.kept]
    assert segments == sorted(segments, key=SEGMENT_ORDER.__getitem__)


@settings(max_examples=120, deadline=None)
@given(
    st.lists(
        st.integers(min_value=TEST_START - DAY_MS, max_value=TEST_START + TEST_SPAN + DAY_MS),
        unique=True,
    )
)
def test_survivors_partition_the_input_into_exactly_one_segment(
    origins: list[int],
) -> None:
    b = _scope()
    report = embargo_purge(
        origins, boundaries=b, horizon_h=H_BARS, embargo_e=E_BARS, bar_ms=BAR_1H
    )
    kept = [a.origin_ms for a in report.kept]
    embargoed = list(report.embargoed)
    purged = list(report.purged)
    # partition: nothing invented, nothing lost, no overlap
    assert sorted(kept + embargoed + purged) == sorted(origins)
    assert set(kept).isdisjoint(embargoed)
    assert set(kept).isdisjoint(purged)
    assert set(embargoed).isdisjoint(purged)
    # every survivor has exactly one valid segment, monotone in origin time
    assert all(a.segment in SEGMENT_ORDER for a in report.kept)
    order = [SEGMENT_ORDER[a.segment] for a in report.kept]
    assert order == sorted(order)
    assert kept == sorted(kept)


@settings(max_examples=120, deadline=None)
@given(st.integers(min_value=TEST_START - DAY_MS, max_value=TEST_START + TEST_SPAN + DAY_MS))
def test_segment_of_is_monotone(origin: int) -> None:
    b = _scope()
    later = origin + DAY_MS
    assert SEGMENT_ORDER[segment_of(origin, b)] <= SEGMENT_ORDER[segment_of(later, b)]


def test_embargo_and_purge_never_move_the_frozen_boundaries() -> None:
    b = _scope()
    before = (b.s_start, b.s_end, b.oos_start, b.holdout_start)
    report = embargo_purge(
        [b.oos_start - 1, b.oos_start, b.holdout_start, b.s_end],
        boundaries=b,
        horizon_h=H_BARS,
        embargo_e=E_BARS,
        bar_ms=BAR_1H,
    )
    assert report.kept or report.embargoed or report.purged
    assert (b.s_start, b.s_end, b.oos_start, b.holdout_start) == before


def test_empty_input_is_accepted() -> None:
    b = _scope()
    report = embargo_purge(
        [], boundaries=b, horizon_h=H_BARS, embargo_e=E_BARS, bar_ms=BAR_1H
    )
    assert report.kept == () and report.embargoed == () and report.purged == ()


# --- result envelopes ---------------------------------------------------------------


def test_boundaries_payload_carries_scope_and_utc_dates() -> None:
    b = load_boundaries(FAMILY, path=PRODUCTION_SCOPE)
    payload = boundaries_payload(b, "DEVELOPMENT")
    assert payload == {
        "data_scope": "DEVELOPMENT",
        "oos_start": "2025-07-20T00:00:00Z",
        "holdout_start": "2026-03-19T00:00:00Z",
    }
    for key in ("oos_start", "holdout_start"):
        parsed = datetime.fromisoformat(payload[key].replace("Z", "+00:00"))
        assert parsed.utcoffset() == UTC.utcoffset(None)
        assert (parsed.hour, parsed.minute, parsed.second) == (0, 0, 0)


def test_scope_file_default_path_exists() -> None:
    # the module's default path is the production file, run from the repo root
    assert (REPO_ROOT / SCOPE_FILE).is_file()
