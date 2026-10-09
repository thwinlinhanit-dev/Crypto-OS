"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getEventEvidence, getStats, postNote } from "@cryptosos/client";
import type { EvidenceView } from "@cryptosos/client";

import { API_BASE } from "@/lib/api";
import { EvidenceGradeBadge } from "@/components/EvidenceGradeBadge";

/**
 * Evidence panel (M5 T5.3, spec UI 558, design m5_stats section 5.2).
 * Opened by clicking a chart marker; the M4 hover card stays hover-only.
 * Every number comes from the evidence payload or the stored stats row
 * (PD-5); the UI never recomputes statistics locally.
 */

function fmt(value: number | null | undefined, digits = 3): string {
  if (value == null || Number.isNaN(value)) return "-";
  return value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function fmtTime(iso: string): string {
  return iso.replace("T", " ").replace("Z", "Z");
}

/** The stored result JSON is loosely typed; narrow one value at a time (PD-5). */
function num(block: Record<string, unknown> | null | undefined, key: string): number | null {
  const value = block?.[key];
  return typeof value === "number" ? value : null;
}

function pair(
  block: Record<string, unknown> | null | undefined,
  key: string,
): [number, number] | null {
  const value = block?.[key];
  return Array.isArray(value) &&
    typeof value[0] === "number" &&
    typeof value[1] === "number"
    ? [value[0], value[1]]
    : null;
}

function countsOf(entry: Record<string, unknown> | undefined): number[] {
  const histogram = entry?.["histogram_r"];
  if (typeof histogram !== "object" || histogram === null) return [];
  const counts = (histogram as Record<string, unknown>)["counts"];
  return Array.isArray(counts) && counts.every((c) => typeof c === "number") ? counts : [];
}

function MultipleTestingBanner({ stats }: { stats: EvidenceView["stats"] }) {
  const mt = stats.multiple_testing as Record<string, unknown>;
  const variantCount = typeof mt.variant_count === "number" ? mt.variant_count : 1;
  const selectionMethod =
    typeof mt.selection_method === "string" ? mt.selection_method : "PRE_SPECIFIED";
  if (variantCount > 1 || selectionMethod !== "PRE_SPECIFIED") {
    return (
      <div className="banner banner-error" role="note">
        Multiple-testing warning: {variantCount} variants evaluated; this result was
        selected after parameter search.
      </div>
    );
  }
  return (
    <div className="banner" role="note">
      Pre-specified analysis: 1 variant evaluated (PRE_SPECIFIED).
    </div>
  );
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

export function EvidencePanel({
  eventId,
  onClose,
  onSelectEvent,
}: {
  eventId: string;
  onClose: () => void;
  onSelectEvent?: (eventId: string) => void;
}) {
  const [segment, setSegment] = useState<"pooled" | "is" | "oos">("pooled");
  const [savedNoteId, setSavedNoteId] = useState<string | null>(null);
  const evidence = useQuery({
    queryKey: ["evidence", eventId],
    queryFn: () => getEventEvidence(eventId, API_BASE),
    staleTime: 60_000,
    retry: 1,
  });
  const refId = evidence.data?.stats.ref_id;
  const fullStats = useQuery({
    queryKey: ["stats", refId],
    queryFn: () => getStats(refId ?? "", API_BASE),
    enabled: refId != null,
    staleTime: 60_000,
    retry: 1,
  });
  const saveNote = useMutation({
    mutationFn: (body: { title: string; body_md: string; experiment_id: null }) =>
      postNote(body, API_BASE),
    onSuccess: (note) => setSavedNoteId(note.note_id),
  });

  if (evidence.isLoading) return <p className="muted">Loading evidence.</p>;
  if (evidence.isError || !evidence.data) {
    return (
      <div className="banner banner-error">
        <span>Evidence unavailable: {evidence.error?.message ?? "unknown error"}</span>
        <button type="button" className="btn" onClick={() => void evidence.refetch()}>
          Retry
        </button>
      </div>
    );
  }
  const body = evidence.data;
  const stats = body.stats;
  const entries = stats.horizons as Record<string, unknown>[];
  const fullEntries = (fullStats.data?.result.horizons as Record<string, unknown>[] | undefined) ?? [];
  const anchorEntry =
    fullEntries.find((entry) => entry["h"] === 10) ?? fullEntries[0];
  const histogramCounts = countsOf(anchorEntry);
  const anchorCi = pair(
    entries.find((entry) => entry["h"] === 10),
    "ci",
  );

  const saveToNotebook = () => {
    setSavedNoteId(null);
    saveNote.mutate({
      title: `${body.event.type} ${body.event.instrument} ${body.event.tf} ${body.event.time}`,
      body_md: [
        `${body.event.type} ${body.event.instrument} ${body.event.tf} ${body.event.time}`,
        "",
        `Evidence grade ${stats.grade ?? "n/a"} (n_eff=${stats.n_eff}).`,
        "",
        `Event ref ids: ${body.event.id}`,
        `Stats ref: ${stats.ref_id}`,
        "",
      ].join("\n"),
      experiment_id: null,
    });
  };

  return (
    <div className="evidence-panel" role="complementary" aria-label="Evidence panel">
      <div className="evidence-header">
        <div>
          <b>{body.event.type}</b> · {body.event.instrument} · {body.event.tf}
          <div className="muted">{fmtTime(body.event.time)}</div>
        </div>
        <div className="btn-row">
          <span className="badge">{stats.data_scope}</span>
          <button type="button" className="btn" onClick={onClose} aria-label="Close evidence">
            ×
          </button>
        </div>
      </div>

      <section aria-label="Why triggered">
        <h4>Why triggered</h4>
        <ul className="checklist">
          {body.trigger.map((rule) => (
            <li key={rule.name} className={rule.passed ? "ok" : "bad"}>
              {rule.passed ? "✓" : "✗"} {rule.name}: {fmt(num(rule, "value"))} vs{" "}
              {fmt(num(rule, "threshold"))}
            </li>
          ))}
        </ul>
        <div className="muted">
          Invalidation {fmt(body.event.invalidation, 4)} · r_basis {body.event.r_basis} ·
          lookback gap {body.event.lookback_gap ? "yes" : "no"}
        </div>
      </section>

      <section aria-label="Evidence grade">
        <EvidenceGradeBadge
          grade={stats.grade}
          nEff={stats.n_eff}
          ci={anchorCi}
          dataScope={stats.data_scope}
        />
        {stats.model_sensitive && (
          <div className="banner banner-error" role="note">
            Model-sensitive: grades differ across cluster models; the lowest is shown.
          </div>
        )}
      </section>

      <section aria-label="Horizons">
        <h4>Horizons</h4>
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
                const h = num(entry, "h") ?? 0;
                const block =
                  segment === "pooled"
                    ? entry
                    : ((entry[segment] as Record<string, unknown> | null) ?? null);
                const median = num(block, "median_r");
                const mean = segment === "pooled" ? null : num(block, "mean_r");
                const win = segment === "pooled" ? num(entry, "win") : num(block, "win_rate");
                const ci = segment === "pooled" ? pair(entry, "ci") : pair(block, "ci_median_r");
                return (
                  <tr key={h}>
                    <td>{h}</td>
                    <td>{fmt(median)}</td>
                    <td>{mean == null ? "—" : fmt(mean)}</td>
                    <td>{win == null ? "—" : `${(100 * win).toFixed(1)}%`}</td>
                    <td>{ci == null ? "—" : `[${fmt(ci[0])}, ${fmt(ci[1])}]`}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="muted">Signal-strength axis: n/a (v1).</p>
      </section>

      <section aria-label="R histogram">
        <h4>R histogram (h=10)</h4>
        {fullStats.isLoading ? (
          <p className="muted">Loading histogram.</p>
        ) : histogramCounts.length > 0 ? (
          <Histogram counts={histogramCounts} />
        ) : (
          <p className="muted">No histogram for this horizon.</p>
        )}
      </section>

      <section aria-label="Analogues">
        <h4>Analogues</h4>
        {body.analogues.length === 0 ? (
          <p className="muted">No earlier peers.</p>
        ) : (
          <ul className="log-list">
            {body.analogues.map((peer) => (
              <li key={peer.event_id}>
                <button
                  type="button"
                  className="btn link"
                  onClick={() => onSelectEvent?.(peer.event_id)}
                >
                  {peer.instrument} · {fmtTime(peer.time)} · fwd_r_10{" "}
                  {peer.fwd_r_10 == null ? "—" : fmt(peer.fwd_r_10)}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <MultipleTestingBanner stats={stats} />

      <section aria-label="Actions">
        <div className="btn-row">
          <Link
            className="btn"
            href={`/events?detector=${encodeURIComponent(body.event.detector)}&event_type=${encodeURIComponent(body.event.type)}`}
          >
            Open in Events
          </Link>
          <button
            type="button"
            className="btn"
            onClick={saveToNotebook}
            disabled={saveNote.isPending}
          >
            {saveNote.isPending ? "Saving." : "Save to Notebook"}
          </button>
          <button type="button" className="btn" disabled title="AI arrives M9">
            Ask Copilot
          </button>
          <button type="button" className="btn" disabled title="Experiments arrive in M5b">
            Add to Experiment
          </button>
        </div>
        {saveNote.isError && (
          <div className="banner banner-error">
            Save failed: {saveNote.error?.message ?? "unknown error"}
          </div>
        )}
        {savedNoteId && (
          <div className="banner">
            Saved to Notebook. <Link href="/research/notebook">Open Notebook →</Link>
          </div>
        )}
      </section>
    </div>
  );
}
