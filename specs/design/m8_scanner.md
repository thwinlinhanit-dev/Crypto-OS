# M8 design note — Scanner, watchlists, MTF, replay

Companion to IMPLEMENTATION_SPEC_v1.1.1 Section 11 (API: `/v1/scanner`,
`/v1/mtf`, `/v1/watchlists`), Section 12 (UI: Scanner + Replay) and Section
15 (milestone M8).

Decisions recorded in `docs/DECISIONS.md` as D-053..D-058; open questions
OQ-24.

---

## 0. Acceptance-criteria mapping

| AC (M8) | Where it lands |
| --- | --- |
| scanner 100 symbols < 2s | `engine/scanner/scan.py` — one pass over the loaded universe with the per-symbol work bounded (no per-symbol HTTP); `tests/test_scanner.py::test_scanner_100_symbols_under_two_seconds` (skip-guarded when the universe is smaller) |
| replay leaks no future data (test) | `engine/scanner/replay.py::as_of` + `tests/test_replay.py` — bars/events/outcomes strictly before `as_of`; events appear when `event_time` is reached |
| MTF matches regime module | `engine/scanner/mtf.py` reuses `engine/features/regime.py` verbatim (no re-implementation); `tests/test_mtf.py` |

---

## 1. Tables (spec 10, migration 0008)

```
market.watchlist(watchlist_id uuid PK, name text)
market.watchlist_item(watchlist_id, instrument_id)
```

CRUD `/v1/watchlists` (spec 11). Watchlists name a universe the scanner can
filter by (`?watchlist=...`); items are (watchlist, instrument) pairs, so
one instrument can sit in many lists.

## 2. Scanner (spec 11 + 12)

`GET /v1/scanner?watchlist&tf&event_types[]&min_grade&filters` returns rows
`{instrument, price, chg_24h, rvol, last_event{type,time,age_bars}, regime,
grade, n_eff}` — the spec UI 560 column list.

- One row per instrument in the universe (watchlist members, or the seeded
  universe when no watchlist is given). Each row is computed from stored
  data only: price = last closed bar, chg_24h = 24h change at that bar,
  rvol = the latest detector-eligible rvol, last_event = the most recent
  emitted event (type, time, age in bars), regime = the regime tags
  (`trend_state` / `vol_state` / `volume_state` — descriptive only, D-017),
  grade + n_eff from the most recent stored stats result for that
  instrument+timeframe when one exists (PD-5: no live compute per row —
  the stored `ref_id` is the source).
- Performance: the scan loads each instrument's bars once and computes the
  row in memory (D-053: one frame per instrument, no N+1 queries; the
  100-symbol budget is asserted in the test suite).
- Virtualized + saved scans + state preserved on the UI side (spec 12);
  `state_preserved` stores the last filter set client-side (D-012 precedent
  — `localStorage` under `cryptosos-scanner-state`).

## 3. MTF (spec 11 `GET /v1/mtf?instrument_id`)

Per-timeframe regime summary for one instrument, reusing
`engine/features/regime.py` verbatim (D-054: no second implementation —
the AC is "MTF matches the regime module", so the module *is* the source).
Each timeframe carries the three regime tags plus the bar count; the
higher-timeframe tags are the current regime context for the chart.

## 4. Replay (spec 12)

`as_of` semantics (D-055): every read the replay view makes carries
`as_of`; bars are filtered to `close_time <= as_of`, events to
`event_time <= as_of`, and outcomes/trades to their fill time. The UI shows
no bars/events/outcomes after `as_of`; events appear when `event_time` is
reached; trades show `signal_time`/`decision_time`/`fill_time` (M6's 7.3
fields). Controls: prev/next/play/pause/speed.

The test asserts the no-future-leak property directly: with `as_of` set to
bar k, the response contains no bar after k and no event with a later
`event_time` (PD-2, the truncation-invariance spirit).

## 5. API (spec 11)

```
CRUD   /v1/watchlists
GET    /v1/watchlists/{id}            (with items)
GET    /v1/scanner?watchlist&tf&event_types[]&min_grade&filters
GET    /v1/mtf?instrument_id
```

Fail-closed: unknown watchlist 404, unknown timeframe 422, unknown
`min_grade` 422 (the A-D vocabulary, D-028), empty result set is an empty
list (not an error — the UI's empty state carries the next action).

## 6. Out of scope (parked)

- `POST /v1/sweeps` — M6 (parked; the sweep runner belongs with the
  backtest simulator, already recorded).
- Live/streaming scanner refresh — the scan is request-scoped; streaming is
  v1.1.
- Saved-scan sharing across devices — localStorage today (D-012 precedent).

## 7. Test plan (tests first)

| Suite | Covers |
| --- | --- |
| `tests/test_watchlists.py` | CRUD + membership; delete-while-referenced 409 |
| `tests/test_scanner.py` | row shape (the 9 spec columns), filters, min_grade 422, the 100-symbol < 2s budget |
| `tests/test_mtf.py` | regime tags match `engine/features/regime.py` per timeframe |
| `tests/test_replay.py` | as_of leaks nothing (bars/events/outcomes strictly before) |

## 8. Decisions to record

| ID | Decision |
| --- | --- |
| D-053 | The scanner loads one frame per instrument (no N+1) and computes rows in memory |
| D-054 | MTF reuses `engine/features/regime.py` verbatim — no second implementation |
| D-055 | Replay `as_of` filters bars/events/outcomes by their stored time fields |
| D-056 | Scanner grade/n_eff come from the stored stats result (PD-5), never a live recompute |
| D-057 | Watchlist items are (watchlist, instrument) pairs; one instrument in many lists |
| D-058 | Saved scans persist client-side in localStorage (the D-012 precedent) |

Open questions for the owner: **OQ-24** (the scanner universe beyond the
seeded BTC/ETH/SOL — OD-2's 30-symbol list is requested before M7/M8;
until it exists the scanner runs on the seeded universe).
