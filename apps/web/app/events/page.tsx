"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { getEvents, getStats, postStats } from "@cryptosos/client";

import { API_BASE } from "@/lib/api";
import { EvidenceGradeBadge } from "@/components/EvidenceGradeBadge";

/**
 * Events Explorer (spec UI 559, design note m5_stats section 5.3): filterable
 * events table (from GET /v1/events with the same query), a live stats pane
 * (POST /v1/stats for the current filter) showing the grade triple, horizons
 * and histogram with IS/OOS columns, plus client-side CSV export including
 * dataset_version and data_scope. Every number comes from a stored or freshly
 * stored server result (PD-5); the UI never computes statistics locally.
 */

const EVENT_TYPES = ["CONT", "CAPIT", "FAIL", "TOP"];
const DIRECTIONS = ["LONG", "SHORT"];
const TIMEFRAMES = ["1D", "4H", "1H"];

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function fmt(value: number | null | undefined, digits = 3): string {
  if (value == null || Number.isNaN(value)) return "-";
  return value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function pair(
  block: Record<string, unknown> | null | undefined,
  key: string,
): string {
  const tuple = pairNum(block, key);
  return tuple == null ? "-" : `[${fmt(tuple[0])}, ${fmt(tuple[1])}]`;
}

function pairNum(
  block: Record<string, unknown> | null | undefined,
  key: string,
): [number, number] | null {
  const value = block?.[key];
  if (!Array.isArray(value) || typeof value[0] !== "number" || typeof value[1] !== "number") {
    return null;
  }
  return [value[0], value[1]];
}

function num(block: Record<string, unknown> | null | undefined, key: string): number | null {
  const value = block?.[key];
  return typeof value === "number" ? value : null;
}

function Histogram({ counts }: { counts: number[] }) {
  const max = Math.max(1, ...counts);
  return (
    <div className="histogram" role="img" aria-label="R histogram">
      {counts.map((count, index) => (
        <div
          key={index}
          className="histogram-bar"
          style={{ height: `${Math.max(2, Math.round((count / max) * 64))}px` }}
          title={`bin ${index}: ${count}`}
        />
      ))}
    </div>
  );
}

export default function EventsExplorerPage() {
  const [instrumentId, setInstrumentId] = useState("");
  const [timeframe, setTimeframe] = useState("1D");
  const [eventType, setEventType] = useState("");
  const [direction, setDirection] = useState("");
  const [segment, setSegment] = useState<"pooled" | "is" | "oos">("pooled");
  const [csvNote, setCsvNote] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["events-explorer", instrumentId, timeframe],
    queryFn: () =>
      getEvents(
        {
          instrument_id: instrumentId,
          tf: timeframe,
          ...(eventType ? { event_type: eventType } : {}),
        },
        API_BASE,
      ),
    enabled: instrumentId !== "",
    staleTime: 30_000,
    retry: 1,
  });

  const stats = useMutation({
    mutationFn: () =>
      postStats(
        {
          filter: {
            detector: "RVOL_CLIMAX",
            timeframe,
            ...(eventType ? { event_type: eventType } : {}),
            ...(direction ? { direction } : {}),
          },
          instrument_ids: [instrumentId],
          data_scope: "DEVELOPMENT",
        },
        API_BASE,
      ),
  });

  const statsDetail = useQuery({
    queryKey: ["stats-detail", stats.data?.ref_id],
    queryFn: () => getStats(stats.data?.ref_id ?? "", API_BASE),
    enabled: stats.data != null,
    staleTime: 60_000,
    retry: 1,
  });

  const run = useCallback(() => {
    setCsvNote(null);
    if (instrumentId !== "") stats.mutate();
  }, [instrumentId, stats]);

  const rows = useMemo(() => {
    const events = query.data?.events ?? [];
    return direction
      ? events.filter((event) => event.direction === direction)
      : events;
  }, [query.data, direction]);

  useEffect(() => {
    if (rows.length > 0) setText(rows[0].event_time);
    else setText(null);
  }, [rows]);

  const entries = (statsDetail.data?.result.horizons as Record<string, unknown>[] | undefined) ?? [];
  const anchorEntry =
    entries.find((entry) => entry["h"] === 10) ?? entries[0];
  const histogramCounts = (() => {
    const histogram = anchorEntry?.["histogram_r"];
    if (typeof histogram !== "object" || histogram === null) return [];
    const counts = (histogram as Record<string, unknown>)["counts"];
    return Array.isArray(counts) && counts.every((c) => typeof c === "number") ? counts : [];
  })();

  const exportCsv = () => {
    if (stats.data == null) {
      setCsvNote("Run the live stats first.");
      return;
    }
    const header = [
      "event_id",
      "event_time",
      "event_type",
      "direction",
      "dataset_version",
      "data_scope",
    ];
    const lines = rows.map((event) =>
      [
        event.event_id,
        event.event_time,
        event.event_type,
        event.direction,
        stats.data.dataset_version,
        stats.data.data_scope,
      ].join(","),
    );
    const blob = new Blob([[header.join(","), ...lines].join("\n")], {
      type: "text/csv",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `events-${stats.data.dataset_version}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    setCsvNote(`Exported ${rows.length} rows.`);
  };

  return (
    <div className="panel">
      <h2>Events Explorer</h2>
      <p className="muted">
        Filter events, run the live stats for the same filter, and export the rows with their
        provenance.
      </p>

      <div className="btn-row">
        <input
          className="chart-select"
          placeholder="Instrument id (UUID)"
          value={instrumentId}
          onChange={(event) => setInstrumentId(event.target.value)}
          aria-label="Instrument id"
        />
        <select
          className="chart-select"
          value={timeframe}
          onChange={(event) => setTimeframe(event.target.value)}
          aria-label="Timeframe"
        >
          {TIMEFRAMES.map((tf) => (
            <option key={tf} value={tf}>
              {tf}
            </option>
          ))}
        </select>
        <select
          className="chart-select"
          value={eventType}
          onChange={(event) => {
            setEventType(event.target.value);
            setText(null);
          }}
          aria-label="Event type"
        >
          <option value="">All types</option>
          {EVENT_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
        <select
          className="chart-select"
          value={direction}
          onChange={(event) => setDirection(event.target.value)}
          aria-label="Direction"
        >
          <option value="">Both directions</option>
          {DIRECTIONS.map((dir) => (
            <option key={dir} value={dir}>
              {dir}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn"
          onClick={run}
          disabled={instrumentId === "" || stats.isPending}
        >
          {stats.isPending ? "Running." : "Run live stats"}
        </button>
        <button type="button" className="btn" onClick={exportCsv}>
          Export CSV
        </button>
      </div>
      {csvNote && <div className="banner">{csvNote}</div>}

      {stats.isError && (
        <div className="banner banner-error">
          <span>Stats failed: {messageOf(stats.error)}</span>
        </div>
      )}

      <section aria-label="Events table">
        <h4>Events</h4>
        {instrumentId === "" ? (
          <p className="muted">Paste an instrument id to list events.</p>
        ) : query.isLoading ? (
          <p className="muted">Loading events.</p>
        ) : query.isError ? (
          <div className="banner banner-error">
            <span>Events unavailable: {messageOf(query.error)}</span>
            <button type="button" className="btn" onClick={() => void query.refetch()}>
              Retry
            </button>
          </div>
        ) : rows.length === 0 ? (
          <p className="muted">No events in this window.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>type</th>
                  <th>time</th>
                  <th>direction</th>
                  <th>ref</th>
                  <th>inval</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((event) => (
                  <tr key={event.event_id}>
                    <td>{event.event_type}</td>
                    <td>{event.event_time.replace("T", " ").replace("Z", "Z")}</td>
                    <td>{event.direction}</td>
                    <td>{fmt(event.ref_price, 2)}</td>
                    <td>{fmt(event.invalidation_price, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {text && <p className="muted">Latest event: {text.replace("T", " ")}Z</p>}
      </section>

      {stats.data && (
        <section aria-label="Live stats">
          <h4>Live StatsResult</h4>
          <EvidenceGradeBadge
            grade={stats.data.grade}
            nEff={stats.data.n_eff}
            ci={pairNum(anchorEntry, "ci")}
            dataScope={stats.data.data_scope}
          />
          {stats.data.model_sensitive && (
            <div className="banner banner-error" role="note">
              Model-sensitive: grades differ across cluster models; the lowest is shown.
            </div>
          )}
          <div className="btn-row" role="group" aria-label="Segment">
            {(["pooled", "is", "oos"] as const).map((key) => (
              <button
                key={key}
                type="button"
                className="btn"
                aria-pressed={segment === key}
                onClick={() => setSegment(key)}
              >
                {key === "pooled" ? "ALL" : key.toUpperCase()}
              </button>
            ))}
          </div>
          {statsDetail.isLoading ? (
            <p className="muted">Loading horizons.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>h</th>
                    <th>median R</th>
                    <th>mean R</th>
                    <th>win%</th>
                    <th>CI</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((entry) => {
                    const block =
                      segment === "pooled"
                        ? entry
                        : ((entry[segment] as Record<string, unknown> | null) ?? null);
                    return (
                      <tr key={String(entry["h"])}>
                        <td>{String(entry["h"])}</td>
                        <td>{fmt(num(block, "median_r"))}</td>
                        <td>{fmt(num(block, "mean_r"))}</td>
                        <td>
                          {num(block, segment === "pooled" ? "win" : "win_rate") == null
                            ? "-"
                            : `${(100 * (num(block, segment === "pooled" ? "win" : "win_rate") as number)).toFixed(1)}%`}
                        </td>
                        <td>
                          {pair(block, segment === "pooled" ? "ci" : "ci_median_r")}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <h4>R histogram (h=10)</h4>
          {histogramCounts.length > 0 ? (
            <Histogram counts={histogramCounts} />
          ) : (
            <p className="muted">No histogram for this horizon.</p>
          )}
          <p className="muted">
            Dataset {stats.data.dataset_version} · scope {stats.data.data_scope}
          </p>
        </section>
      )}
    </div>
  );
}
