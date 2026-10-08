"""Spec section 4.6: Data Guardian checks, states, completeness, exclusions.

Thresholds are quoted from section 4.6. Where the spec is silent the test says
so and carries the open question instead of inventing a rule (PD-4):
- grace for the stale feed check has no spec value (ASSUMPTION, design note 13);
- zero-volume >=0.1% has no spec state (OQ-5): reported YELLOW with the fraction;
- non-monotonic time has no named state; section 4.6's RED covers corrupt bars
  ("OHLC violations/duplicates unresolved") and ordering corruption is treated
  as invalid (INTERPRETATION, DECISIONS).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from apps.api.settings import get_settings
from engine.data.bars import bar_frame
from engine.data.guardian import (
    ExclusionInterval,
    ExclusionKind,
    GapRow,
    QualityState,
    assess,
    latest_assessments,
    load_exclusions,
    save_assessment,
    save_exclusions,
    set_gap_status,
)
from engine.data.instruments import build_instrument, upsert_instruments

DAY = 86_400_000
#: 2024-01-01T00:00:00Z (a Monday) - weekly bars align to Mondays (section 4.3).
W0 = 1_704_067_200_000

INSTRUMENT_SEED = build_instrument(
    venue="BINANCE", market_type="SPOT", asset="BTC", quote="USDT", native_symbol="BTCUSDT"
)


def _row(open_ms: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "instrument_id": str(INSTRUMENT_SEED.instrument_id),
        "timeframe": "1D",
        "open_time": open_ms,
        "close_time": open_ms + DAY,
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 10.0,
        "quote_volume": 1050.0,
        "trades": 100,
        "source": "fixture",
    }
    row.update(overrides)
    return row


def _frame(rows: list[dict[str, Any]], *, ingested_at_ms: int) -> Any:
    return bar_frame(rows, ingested_at_ms=ingested_at_ms)


def _ten_day_frame(**frame_kwargs: Any) -> Any:
    opens = [W0 + i * DAY for i in range(10)]
    return _frame([_row(open_ms) for open_ms in opens], ingested_at_ms=W0 + 10 * DAY, **frame_kwargs)


def _assess(frame: Any, *, now_ms: int, end_ms: int, start_ms: int = W0, ingested_at_ms: int | None = None) -> Any:
    return assess(
        frame,
        timeframe="1D",
        window_start_ms=start_ms,
        window_end_ms=end_ms,
        now_ms=now_ms,
        ingested_at_ms=ingested_at_ms,
    )


# --------------------------------------------------------------------------
# States
# --------------------------------------------------------------------------


def test_complete_series_is_green_with_no_exclusions() -> None:
    result = _assess(_ten_day_frame(), now_ms=W0 + 10 * DAY, end_ms=W0 + 9 * DAY)
    assert result.state is QualityState.GREEN
    assert result.completeness == 1.0
    assert result.exclusions == ()
    assert result.stale is False
    assert result.zero_volume_fraction == 0.0
    assert result.detail["causes"] == []


def test_missing_bar_is_orange_and_excludes_only_that_bar() -> None:
    missing_open = W0 + 5 * DAY
    rows = [_row(W0 + i * DAY) for i in range(10) if i != 5]
    frame = _frame(rows, ingested_at_ms=W0 + 10 * DAY)

    result = _assess(frame, now_ms=W0 + 10 * DAY, end_ms=W0 + 9 * DAY)

    assert result.state is QualityState.ORANGE
    assert result.detail["causes"] == ["open_gaps"]
    assert (result.expected_bars, result.present_bars) == (10, 9)
    assert result.completeness == pytest.approx(0.9)
    # Section 4.6: the exclusion spans exactly [missing open, missing close].
    assert len(result.exclusions) == 1
    gap = result.exclusions[0]
    assert gap.kind is ExclusionKind.GAP
    assert (gap.start_ms, gap.end_ms) == (missing_open, missing_open + DAY)


def test_ohlc_violation_is_red_and_excludes_only_the_bad_bar() -> None:
    bad_open = W0 + 3 * DAY
    # close > high violates "c<=h" from the spec's OHLC sanity triple.
    rows = [_row(open_ms, **({"close": 120.0} if open_ms == bad_open else {})) for open_ms in (W0 + i * DAY for i in range(10))]
    frame = _frame(rows, ingested_at_ms=W0 + 10 * DAY)

    result = _assess(frame, now_ms=W0 + 10 * DAY, end_ms=W0 + 9 * DAY)

    assert result.state is QualityState.RED
    assert result.detail["causes"] == ["ohlc_violations"]
    assert result.detail["checks"]["ohlc"]["violations"] == 1
    assert result.detail["completeness"] == {"present": 10, "expected": 10, "fraction": 1.0}
    assert len(result.exclusions) == 1
    invalid = result.exclusions[0]
    assert invalid.kind is ExclusionKind.INVALID
    assert (invalid.start_ms, invalid.end_ms) == (bad_open, bad_open + DAY)


def test_duplicate_bar_is_red() -> None:
    rows = [_row(W0 + i * DAY) for i in range(10)]
    rows.insert(5, _row(W0 + 4 * DAY, volume=99.0))  # same open, different row
    frame = _frame(rows, ingested_at_ms=W0 + 10 * DAY)

    result = _assess(frame, now_ms=W0 + 10 * DAY, end_ms=W0 + 9 * DAY)

    assert result.state is QualityState.RED
    assert "duplicate_bars" in result.detail["causes"]
    assert result.detail["checks"]["duplicates"]["violations"] == 1
    # No missing bars: the only exclusion is the duplicated open's interval.
    assert len(result.exclusions) == 1
    assert result.exclusions[0].kind is ExclusionKind.INVALID
    assert (result.exclusions[0].start_ms, result.exclusions[0].end_ms) == (W0 + 4 * DAY, W0 + 5 * DAY)


def test_out_of_order_arrival_is_non_monotonic_and_red() -> None:
    # Same ten bars, but day 2 arrives after day 3: nothing is missing or
    # duplicated, only the order is wrong (section 4.6 non-monotonic time).
    order = [0, 1, 3, 2, 4, 5, 6, 7, 8, 9]
    rows = [_row(W0 + i * DAY) for i in order]
    frame = _frame(rows, ingested_at_ms=W0 + 10 * DAY)

    result = _assess(frame, now_ms=W0 + 10 * DAY, end_ms=W0 + 9 * DAY)

    assert result.state is QualityState.RED
    assert "non_monotonic_time" in result.detail["causes"]
    assert result.detail["checks"]["non_monotonic"]["opens"] == [W0 + 2 * DAY]
    assert result.detail["checks"]["gaps"]["missing"] == 0
    assert len(result.exclusions) == 1
    assert (result.exclusions[0].start_ms, result.exclusions[0].end_ms) == (W0 + 2 * DAY, W0 + 3 * DAY)


def test_ohlc_check_is_exactly_the_three_relations_the_spec_lists() -> None:
    # Section 4.6 sanity is "(l<=o, c<=h, l<=h)". Open above high breaks none
    # of those three, so it must NOT be flagged: inventing a fourth relation
    # would invent a rule (PD-4). The gap is carried as an open question.
    frame = _frame([_row(W0, open=120.0, high=110.0, low=95.0, close=105.0)], ingested_at_ms=W0 + DAY)

    result = _assess(frame, now_ms=W0 + DAY, end_ms=W0)

    assert result.state is QualityState.GREEN
    assert result.detail["checks"]["ohlc"]["violations"] == 0


def test_zero_volume_below_point_one_percent_is_yellow() -> None:
    # Section 4.6: "YELLOW degraded (zero-volume bars <0.1%)".
    rows = [_row(W0 + i * DAY) for i in range(1000)]
    rows[500] = _row(W0 + 500 * DAY, volume=0.0, quote_volume=0.0, trades=0)
    frame = _frame(rows, ingested_at_ms=W0 + 1000 * DAY)

    result = _assess(frame, now_ms=W0 + 1000 * DAY, end_ms=W0 + 999 * DAY)

    assert result.state is QualityState.YELLOW
    assert result.detail["causes"] == ["zero_volume_bars"]
    assert result.zero_volume_fraction == pytest.approx(1 / 1000)
    assert result.completeness == 1.0


def test_zero_volume_at_or_above_point_one_percent_stays_yellow_with_its_fraction() -> None:
    # OQ-5: section 4.6 gives no state for a fraction >=0.1%. Reported YELLOW
    # (degraded, not invalid/incomplete) with the exact fraction in detail.
    rows = [_row(W0 + i * DAY) for i in range(100)]
    for index in (10, 20, 30, 40, 50):
        rows[index] = _row(W0 + index * DAY, volume=0.0, quote_volume=0.0, trades=0)
    frame = _frame(rows, ingested_at_ms=W0 + 100 * DAY)

    result = _assess(frame, now_ms=W0 + 100 * DAY, end_ms=W0 + 99 * DAY)

    assert result.state is QualityState.YELLOW
    assert result.zero_volume_fraction == pytest.approx(0.05)


def test_stale_feed_is_yellow_but_a_frozen_history_is_not() -> None:
    rows = [_row(W0 + i * DAY) for i in range(10)]

    # Ingested while the last bar was current, but now is 10 bar-lengths later
    # with no new bars: "no new closed bar within 2 bar-lengths + grace".
    # Grace value: the spec gives none (ASSUMPTION, STALE_GRACE_BAR_LENGTHS=1).
    live = _frame(rows, ingested_at_ms=W0 + 10 * DAY)
    stale_result = _assess(live, now_ms=W0 + 20 * DAY, end_ms=W0 + 9 * DAY)
    assert stale_result.state is QualityState.YELLOW
    assert stale_result.stale is True
    assert stale_result.detail["causes"] == ["stale_feed"]
    assert stale_result.detail["checks"]["stale"]["threshold_ms"] == 3 * DAY

    # Same bars ingested long after they closed: a frozen historical range is
    # not a stale feed - nothing is wrong with it.
    frozen = _frame(rows, ingested_at_ms=W0 + 200 * DAY)
    frozen_result = _assess(frozen, now_ms=W0 + 200 * DAY, end_ms=W0 + 9 * DAY)
    assert frozen_result.state is QualityState.GREEN
    assert frozen_result.stale is False


def test_red_takes_precedence_over_the_gaps_in_the_same_range() -> None:
    rows = [
        _row(W0 + i * DAY, **({"close": 120.0} if i == 3 else {}))
        for i in range(10)
        if i != 5
    ]
    frame = _frame(rows, ingested_at_ms=W0 + 10 * DAY)

    result = _assess(frame, now_ms=W0 + 10 * DAY, end_ms=W0 + 9 * DAY)

    assert result.state is QualityState.RED
    # Every observed condition is reported even though only one drives the state.
    assert result.detail["causes"] == ["ohlc_violations", "open_gaps"]
    kinds = {interval.kind for interval in result.exclusions}
    assert kinds == {ExclusionKind.INVALID, ExclusionKind.GAP}


# --------------------------------------------------------------------------
# Completeness and window semantics
# --------------------------------------------------------------------------


def test_completeness_is_present_over_expected_in_the_requested_range() -> None:
    rows = [_row(W0 + i * DAY) for i in range(5)]  # data covers half the window
    frame = _frame(rows, ingested_at_ms=W0 + 10 * DAY)

    result = _assess(frame, now_ms=W0 + 10 * DAY, end_ms=W0 + 9 * DAY)

    assert (result.expected_bars, result.present_bars) == (10, 5)
    assert result.completeness == pytest.approx(0.5)
    # The five missing days form one contiguous run: one exclusion interval.
    assert len(result.exclusions) == 1
    assert (result.exclusions[0].start_ms, result.exclusions[0].end_ms) == (W0 + 5 * DAY, W0 + 10 * DAY)


def test_forming_bar_is_never_counted_as_expected() -> None:
    frame = _ten_day_frame()  # bars for days 0..9 only
    # Window asks for an 11th bar whose close (W0+11d) is after now.
    result = _assess(frame, now_ms=W0 + 10 * DAY, end_ms=W0 + 10 * DAY)

    assert result.expected_bars == 10
    assert result.completeness == 1.0
    assert result.state is QualityState.GREEN


def test_empty_frame_with_expected_closed_bars_is_orange() -> None:
    frame = _frame([], ingested_at_ms=W0 + 10 * DAY)

    result = _assess(frame, now_ms=W0 + 10 * DAY, end_ms=W0 + 9 * DAY)

    assert result.state is QualityState.ORANGE
    assert result.completeness == 0.0
    assert (result.expected_bars, result.present_bars) == (10, 0)
    assert len(result.exclusions) == 1
    assert (result.exclusions[0].start_ms, result.exclusions[0].end_ms) == (W0, W0 + 10 * DAY)


# --------------------------------------------------------------------------
# Persistence (system.data_quality, system.gap)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def tx(engine: Engine) -> Iterator[Connection]:
    conn = engine.connect()
    conn.begin()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def instrument_id(tx: Connection) -> UUID:
    upsert_instruments(tx, [INSTRUMENT_SEED])
    return INSTRUMENT_SEED.instrument_id


def test_assessment_round_trips_through_postgres(tx: Connection, instrument_id: UUID) -> None:
    result = _assess(_ten_day_frame(), now_ms=W0 + 10 * DAY, end_ms=W0 + 9 * DAY)
    save_assessment(tx, instrument_id, result)

    rows = latest_assessments(tx)
    assert len(rows) == 1
    row = rows[0]
    assert row.instrument_id == instrument_id
    assert row.timeframe == "1D"
    assert row.state is QualityState.GREEN
    assert float(row.completeness) == pytest.approx(1.0)
    # jsonb round trip must be lossless: detail is the evidence the UI shows.
    assert row.detail == json.loads(json.dumps(result.detail))


def test_latest_assessment_per_series_wins(tx: Connection, instrument_id: UUID) -> None:
    good = _assess(_ten_day_frame(), now_ms=W0 + 10 * DAY, end_ms=W0 + 9 * DAY)
    save_assessment(tx, instrument_id, good, computed_at=datetime(2024, 1, 1, tzinfo=UTC))

    rows = [_row(W0 + i * DAY) for i in range(10) if i != 5]
    bad = _assess(_frame(rows, ingested_at_ms=W0 + 10 * DAY), now_ms=W0 + 10 * DAY, end_ms=W0 + 9 * DAY)
    assert bad.state is QualityState.ORANGE
    save_assessment(tx, instrument_id, bad, computed_at=datetime(2024, 1, 2, tzinfo=UTC))

    latest = latest_assessments(tx)
    assert len(latest) == 1  # one row per (instrument, timeframe), not per history
    assert latest[0].state is QualityState.ORANGE
    assert latest[0].computed_at == datetime(2024, 1, 2, tzinfo=UTC)

    history = tx.execute(
        text("SELECT count(*) FROM system.data_quality WHERE instrument_id = :id"),
        {"id": instrument_id},
    ).scalar_one()
    assert history == 2  # history itself is append-only


def test_exclusions_are_idempotent_and_only_open_rows_exclude(
    tx: Connection, instrument_id: UUID
) -> None:
    interval = ExclusionInterval(W0 + 5 * DAY, W0 + 6 * DAY, ExclusionKind.GAP)

    first = save_exclusions(tx, instrument_id=instrument_id, timeframe="1D", intervals=[interval])
    again = save_exclusions(tx, instrument_id=instrument_id, timeframe="1D", intervals=[interval])
    assert len(first) == 1
    assert again == []

    active = load_exclusions(tx, instrument_id=instrument_id, active_only=True)
    assert len(active) == 1
    assert active[0].status == "OPEN"
    assert active[0].source == "guardian"
    assert active[0].exclusion_start == datetime.fromtimestamp((W0 + 5 * DAY) / 1000, tz=UTC)
    assert active[0].exclusion_end == datetime.fromtimestamp((W0 + 6 * DAY) / 1000, tz=UTC)

    # Repair closes the row: it stops excluding but stays listed for reports.
    assert set_gap_status(
        tx,
        first[0],
        status="REPAIRED",
        repaired_at=datetime(2024, 1, 3, tzinfo=UTC),
        repaired_dataset_version="v-fixed",
    )
    assert load_exclusions(tx, instrument_id=instrument_id, active_only=True) == []
    listed = load_exclusions(tx, instrument_id=instrument_id)
    assert [row.status for row in listed] == ["REPAIRED"]
    assert listed[0].repaired_dataset_version == "v-fixed"

    # Re-detection after repair inserts a FRESH open row (section 4.6 repair).
    redetected = save_exclusions(tx, instrument_id=instrument_id, timeframe="1D", intervals=[interval])
    assert len(redetected) == 1
    assert len(load_exclusions(tx, instrument_id=instrument_id, active_only=True)) == 1
    assert len(load_exclusions(tx, instrument_id=instrument_id)) == 2


def test_unknown_gap_id_is_not_marked_repaired(tx: Connection) -> None:
    assert set_gap_status(tx, uuid4(), status="REPAIRED") is False


def test_unrepairable_ranges_stay_active_and_keep_their_kind(
    tx: Connection, instrument_id: UUID
) -> None:
    gap_ids = save_exclusions(
        tx,
        instrument_id=instrument_id,
        timeframe="1D",
        intervals=[
            ExclusionInterval(W0, W0 + DAY, ExclusionKind.GAP),
            ExclusionInterval(W0 + 2 * DAY, W0 + 3 * DAY, ExclusionKind.INVALID),
        ],
    )
    assert len(gap_ids) == 2

    listed = load_exclusions(tx, instrument_id=instrument_id)
    assert [row.kind for row in listed] == ["gap", "invalid"]

    # Section 4.6: an UNREPAIRABLE range "stays excluded", so active_only must
    # keep it while a REPAIRED one stops excluding (design note 6.1, amended).
    assert set_gap_status(tx, gap_ids[0], status="UNREPAIRABLE")
    active = load_exclusions(tx, instrument_id=instrument_id, active_only=True)
    assert len(active) == 2
    assert {row.status for row in active} == {"OPEN", "UNREPAIRABLE"}


def test_gap_rows_are_typed_end_to_end(tx: Connection, instrument_id: UUID) -> None:
    save_exclusions(
        tx,
        instrument_id=instrument_id,
        timeframe="1D",
        intervals=[ExclusionInterval(W0, W0 + DAY, ExclusionKind.INVALID)],
    )
    row: GapRow = load_exclusions(tx)[0]
    assert isinstance(row, GapRow)
    assert row.timeframe == "1D"
    assert row.instrument_id == instrument_id
    assert row.repaired_at is None
