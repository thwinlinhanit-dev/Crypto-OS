/**
 * M8 — Scanner (spec UI 560): the spec-560 column table, virtualized,
 * with saved scans and preserved state (D-058).
 *
 * PD-5: every number comes from a stored server result; the page never
 * computes statistics. Clicking a row opens the MTF panel for that
 * instrument.
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteWatchlist, getMtf, getScanner, getWatchlists } from "@cryptosos/client";

import { API_BASE } from "@/lib/api";

/** The saved-scan state key (D-058: localStorage, the D-012 precedent). */
const STATE_KEY = "cryptosos-scanner-state";

interface ScanState {
  watchlist: string;
  tf: string;
  minGrade: string;
}

const DEFAULT_STATE: ScanState = { watchlist: "", tf: "1D", minGrade: "" };

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function fmt(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "-";
  return value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function gradeTone(grade: string | null | undefined): string {
  if (grade == null) return "muted";
  if (grade === "A" || grade === "B") return "ok";
  if (grade === "C") return "warn";
  return "bad";
}

function loadState(): ScanState {
  if (typeof window === "undefined") return DEFAULT_STATE;
  try {
    const raw = window.localStorage.getItem(STATE_KEY);
    if (raw == null) return DEFAULT_STATE;
    const parsed = JSON.parse(raw) as Partial<ScanState>;
    return {
      watchlist: typeof parsed.watchlist === "string" ? parsed.watchlist : "",
      tf: typeof parsed.tf === "string" ? parsed.tf : "1D",
      minGrade: typeof parsed.minGrade === "string" ? parsed.minGrade : "",
    };
  } catch {
    return DEFAULT_STATE;
  }
}

/** The MTF panel for one instrument (regime per timeframe, D-054). */
function MtfPanel({ instrumentId }: { instrumentId: string }) {
  const mtf = useQuery({
    queryKey: ["mtf", instrumentId],
    queryFn: () => getMtf(instrumentId, API_BASE),
    staleTime: 60_000,
    retry: 1,
  });
  if (mtf.isLoading) return <p className="muted">Loading regime…</p>;
  if (mtf.isError) {
    return (
      <div className="banner banner-error">
        <span>MTF failed: {messageOf(mtf.error)}</span>
      </div>
    );
  }
  const body = (mtf.data ?? {}) as Record<string, unknown>;
  const timeframes = (body.timeframes ?? {}) as Record<string, Record<string, unknown>>;
  return (
    <div>
      <h4>
        MTF · {String(body.symbol ?? instrumentId)}
      </h4>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>tf</th>
              <th>bars</th>
              <th>trend</th>
              <th>vol</th>
              <th>volume</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(timeframes).map(([tf, row]) => (
              <tr key={tf}>
                <td>{tf}</td>
                <td className="numeric">{String(row.bars ?? "-")}</td>
                <td>{String(row.trend_state ?? "-")}</td>
                <td>{String(row.vol_state ?? "-")}</td>
                <td>{String(row.volume_state ?? "-")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function ScannerPage() {
  const queryClient = useQueryClient();
  const [state, setState] = useState<ScanState>(DEFAULT_STATE);
  const [selected, setSelected] = useState<string | null>(null);

  // D-058: restore the saved scan on mount, persist on change.
  useEffect(() => setState(loadState()), []);
  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STATE_KEY, JSON.stringify(state));
    }
  }, [state]);

  const scanner = useQuery({
    queryKey: ["scanner", state.watchlist, state.tf, state.minGrade],
    queryFn: () =>
      getScanner(
        {
          watchlist: state.watchlist || undefined,
          tf: state.tf,
          min_grade: state.minGrade || undefined,
        },
        API_BASE,
      ),
    staleTime: 15_000,
    retry: 1,
  });

  const watchlists = useQuery({
    queryKey: ["watchlists-scanner"],
    queryFn: () => getWatchlists(API_BASE),
    staleTime: 30_000,
    retry: 1,
  });

  const remove = useCallback(
    async (watchlistId: string) => {
      await deleteWatchlist(watchlistId, API_BASE);
      setState((current) => ({ ...current, watchlist: "" }));
      void queryClient.invalidateQueries({ queryKey: ["watchlists-scanner"] });
    },
    [queryClient],
  );

  const body = (scanner.data ?? {}) as Record<string, unknown>;
  const rows = (body.rows ?? []) as Record<string, unknown>[];

  return (
    <div className="panel">
      <h2>Scanner</h2>
      <p className="muted">
        Symbol · Price · 24h · RVOL · Last event · Age · Regime · Grade · n_eff — from stored
        results only (PD-5). Click a row for its multi-timeframe regime.
      </p>

      <div className="btn-row" role="group" aria-label="Scanner filters">
        <select
          className="chart-select"
          value={state.watchlist}
          onChange={(event) => setState({ ...state, watchlist: event.target.value })}
          aria-label="Watchlist"
        >
          <option value="">All instruments</option>
          {((watchlists.data ?? []) as Record<string, unknown>[]).map((row) => (
            <option key={String(row.watchlist_id)} value={String(row.watchlist_id)}>
              {String(row.name)}
            </option>
          ))}
        </select>
        <select
          className="chart-select"
          value={state.tf}
          onChange={(event) => setState({ ...state, tf: event.target.value })}
          aria-label="Timeframe"
        >
          {["1D", "4H", "1H"].map((tf) => (
            <option key={tf} value={tf}>
              {tf}
            </option>
          ))}
        </select>
        <select
          className="chart-select"
          value={state.minGrade}
          onChange={(event) => setState({ ...state, minGrade: event.target.value })}
          aria-label="Minimum grade"
        >
          <option value="">Any grade</option>
          {["A", "B", "C", "D"].map((grade) => (
            <option key={grade} value={grade}>
              {grade} or better
            </option>
          ))}
        </select>
        {state.watchlist !== "" && (
          <button type="button" className="btn" onClick={() => void remove(state.watchlist)}>
            Delete watchlist
          </button>
        )}
      </div>

      {scanner.isLoading ? (
        <p className="muted">Scanning…</p>
      ) : scanner.isError ? (
        <div className="banner banner-error">
          <span>Scan failed: {messageOf(scanner.error)}</span>
          <button type="button" className="btn" onClick={() => void scanner.refetch()}>
            Retry
          </button>
        </div>
      ) : rows.length === 0 ? (
        <p className="muted">
          No rows for this filter. Seed fixtures (<code>make seed-fixtures</code>) or widen the
          watchlist.
        </p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>symbol</th>
                <th>price</th>
                <th>24h</th>
                <th>rvol</th>
                <th>last event</th>
                <th>age</th>
                <th>regime</th>
                <th>grade</th>
                <th>n_eff</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const event = (row.last_event ?? null) as Record<string, unknown> | null;
                const regime = (row.regime ?? {}) as Record<string, unknown>;
                const instrument = String(row.instrument ?? "");
                return (
                  <tr
                    key={instrument}
                    className={instrument === selected ? "row-selected" : undefined}
                  >
                    <td>
                      <button
                        type="button"
                        className="btn link"
                        onClick={() =>
                          setSelected(instrument === selected ? null : instrument)
                        }
                      >
                        {String(row.symbol ?? instrument)}
                      </button>
                    </td>
                    <td className="numeric">{fmt(row.price as number | null)}</td>
                    <td className="numeric">
                      {row.chg_24h == null
                        ? "-"
                        : `${((row.chg_24h as number) * 100).toFixed(2)}%`}
                    </td>
                    <td className="numeric">{fmt(row.rvol as number | null)}</td>
                    <td>{event == null ? "-" : String(event.type ?? "-")}</td>
                    <td className="numeric">
                      {event == null ? "-" : String(event.age_bars ?? "-")}
                    </td>
                    <td>
                      {String(regime.trend_state ?? "-")} / {String(regime.vol_state ?? "-")} /{" "}
                      {String(regime.volume_state ?? "-")}
                    </td>
                    <td className={gradeTone(row.grade as string | null)}>
                      {String(row.grade ?? "-")}
                    </td>
                    <td className="numeric">
                      {row.n_eff == null ? "-" : String(row.n_eff)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {selected != null && (
        <section aria-label="Multi-timeframe">
          <MtfPanel instrumentId={selected} />
        </section>
      )}
    </div>
  );
}
