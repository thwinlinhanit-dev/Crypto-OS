"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  getInstruments,
  getDataGaps,
  getDataHealth,
  getJob,
  postIngest,
  postRepair,
} from "@cryptosos/client";
import type { GapView, HealthView, InstrumentView, JobView } from "@cryptosos/client";

import { API_BASE } from "@/lib/api";

/**
 * Data Health (design note section 8): one row per (instrument, timeframe),
 * active exclusion intervals, the gap list with a Repair action, and the
 * seed-universe Ingest action. Every fetch renders the five states of
 * section 12: loading, empty (with the next action), error + retry,
 * stale/offline, and partial data.
 */

const SEED_ASSETS = ["BTC", "ETH", "SOL"];
const SEED_TIMEFRAMES = ["1D", "4H", "1H"];
const SEED_FROM_ISO = "2024-01-01T00:00:00.000Z";
const REFRESH_MS = 5000;
const JOB_POLL_MS = 1500;

type Section<T> = { data: T | null; error: string | null };

function section<T>(): Section<T> {
  return { data: null, error: null };
}

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function utc(iso: string): string {
  return `${iso.slice(0, 19).replace("T", " ")}Z`;
}

function percent(fraction: number): string {
  return `${(fraction * 100).toFixed(1)}%`;
}

const STATE_TONE: Record<string, string> = {
  GREEN: "green",
  YELLOW: "yellow",
  ORANGE: "orange",
  RED: "red",
};

function StateBadge({ state }: { state: string }) {
  // The word is always the label; colour is never the only signal (WCAG).
  const tone = STATE_TONE[state] ?? "neutral";
  return <span className={`badge badge-${tone}`}>{state}</span>;
}

export default function DataHealthPage() {
  const [health, setHealth] = useState<Section<HealthView>>(section);
  const [gaps, setGaps] = useState<Section<GapView[]>>(section);
  const [registry, setRegistry] = useState<Section<InstrumentView[]>>(section);
  const [loadedAt, setLoadedAt] = useState<number | null>(null);
  const [job, setJob] = useState<JobView | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    const [healthResult, gapsResult, registryResult] = await Promise.allSettled([
      getDataHealth({}, API_BASE),
      getDataGaps({}, API_BASE),
      getInstruments({ venue: "BINANCE", market_type: "SPOT" }, API_BASE),
    ]);
    if (healthResult.status === "fulfilled") {
      setHealth({ data: healthResult.value, error: null });
    } else {
      const message = messageOf(healthResult.reason);
      setHealth((previous) => ({ data: previous.data, error: message }));
    }
    if (gapsResult.status === "fulfilled") {
      setGaps({ data: gapsResult.value, error: null });
    } else {
      const message = messageOf(gapsResult.reason);
      setGaps((previous) => ({ data: previous.data, error: message }));
    }
    if (registryResult.status === "fulfilled") {
      setRegistry({ data: registryResult.value, error: null });
    } else {
      const message = messageOf(registryResult.reason);
      setRegistry((previous) => ({ data: previous.data, error: message }));
    }
    if (healthResult.status === "fulfilled" || gapsResult.status === "fulfilled" || registryResult.status === "fulfilled") {
      setLoadedAt(Date.now());
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  // Job polling: refresh the sections as soon as the job reaches a final state.
  const jobDone = job !== null && (job.status === "succeeded" || job.status === "failed");
  useEffect(() => {
    if (job === null || jobDone) {
      if (jobDone) void refresh();
      return;
    }
    const timer = setInterval(() => {
      getJob(job.job_id, API_BASE)
        .then(setJob)
        .catch((cause: unknown) => setActionError(messageOf(cause)));
    }, JOB_POLL_MS);
    return () => clearInterval(timer);
  }, [job, jobDone, refresh]);

  const sections: Section<unknown>[] = [health, gaps, registry];
  const anyResolved = sections.some((entry) => entry.data !== null);
  const failing = sections.filter((entry) => entry.error !== null);
  const anyError = failing.length > 0;
  // Stale/offline: every failing section still shows its last good data.
  // Partial: at least one section lost entirely (or never arrived).
  const stale = anyResolved && failing.length > 0 && failing.every((entry) => entry.data !== null);

  const byId = new Map<string, InstrumentView>();
  for (const instrument of registry.data ?? []) {
    byId.set(instrument.instrument_id, instrument);
  }
  const label = (instrumentId: string): string => {
    const instrument = byId.get(instrumentId);
    return instrument ? `${instrument.native_symbol} (${instrument.asset})` : instrumentId.slice(0, 8);
  };

  const seedInstruments = (registry.data ?? []).filter((instrument) =>
    SEED_ASSETS.includes(instrument.asset),
  );
  const seedComplete = seedInstruments.length === SEED_ASSETS.length;

  async function handleIngest(): Promise<void> {
    setSubmitting(true);
    setActionError(null);
    try {
      const queued = await postIngest({
        venue: "BINANCE",
        market_type: "SPOT",
        instrument_ids: seedInstruments.map((instrument) => instrument.instrument_id),
        timeframes: SEED_TIMEFRAMES,
        from: SEED_FROM_ISO,
        to: new Date().toISOString(),
      }, API_BASE);
      setJob(await getJob(queued.job_id, API_BASE));
    } catch (cause) {
      setActionError(messageOf(cause));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRepair(gapId: string): Promise<void> {
    setSubmitting(true);
    setActionError(null);
    try {
      const queued = await postRepair({ gap_id: gapId }, API_BASE);
      setJob(await getJob(queued.job_id, API_BASE));
    } catch (cause) {
      setActionError(messageOf(cause));
    } finally {
      setSubmitting(false);
    }
  }

  if (!anyResolved && anyError) {
    const firstError = sections.find((entry) => entry.error !== null)?.error ?? "unknown error";
    return (
      <section className="panel">
        <h1>Data Health</h1>
        <div className="banner banner-error">
          <span>{firstError} — start the API with make dev-api.</span>
          <button className="btn" type="button" onClick={() => void refresh()}>
            Retry
          </button>
        </div>
      </section>
    );
  }

  if (!anyResolved) {
    return (
      <section className="panel">
        <h1>Data Health</h1>
        <p className="section-note" aria-busy="true">
          Loading Data Health…
        </p>
      </section>
    );
  }

  const series = health.data?.series ?? [];
  const exclusions = health.data?.exclusions ?? [];
  const allGaps = gaps.data ?? [];

  return (
    <>
      <section className="panel">
        <h1>Data Health</h1>
        {anyError ? (
          <div className={`banner ${stale ? "banner-stale" : "banner-partial"}`}>
            <span>
              {stale
                ? `Showing the last good read${
                    loadedAt !== null
                      ? ` from ${new Date(loadedAt).toISOString().slice(11, 19)}Z`
                      : ""
                  } — API unreachable. `
                : "Partial data — "}
              {[
                health.error !== null ? `health: ${health.error}` : null,
                gaps.error !== null ? `gaps: ${gaps.error}` : null,
                registry.error !== null ? `registry: ${registry.error}` : null,
              ]
                .filter((entry): entry is string => entry !== null)
                .join("; ")}
            </span>
            <button className="btn" type="button" onClick={() => void refresh()}>
              Retry
            </button>
          </div>
        ) : null}
        {series.length === 0 ? (
          <p className="section-note">
            No assessments yet — ingest the seed universe below, then refresh.
          </p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Series</th>
                  <th>Timeframe</th>
                  <th>State</th>
                  <th>Completeness</th>
                  <th>Open gaps</th>
                  <th>Last check</th>
                </tr>
              </thead>
              <tbody>
                {series.map((row) => {
                  const open = exclusions.filter(
                    (gap) =>
                      gap.instrument_id === row.instrument_id &&
                      gap.timeframe === row.timeframe &&
                      gap.status === "OPEN",
                  ).length;
                  return (
                    <tr key={`${row.instrument_id}-${row.timeframe}`}>
                      <td>{label(row.instrument_id)}</td>
                      <td>{row.timeframe}</td>
                      <td>
                        <StateBadge state={row.state} />
                      </td>
                      <td className="numeric">{percent(row.completeness)}</td>
                      <td className="numeric">{open}</td>
                      <td>{utc(row.computed_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <h1>Active exclusion intervals</h1>
        {exclusions.length === 0 ? (
          <p className="section-note">None active.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Series</th>
                  <th>Timeframe</th>
                  <th>Start</th>
                  <th>End</th>
                  <th>Status</th>
                  <th>Kind</th>
                </tr>
              </thead>
              <tbody>
                {exclusions.map((gap) => (
                  <tr key={gap.gap_id}>
                    <td>{label(gap.instrument_id)}</td>
                    <td>{gap.timeframe}</td>
                    <td>{utc(gap.exclusion_start)}</td>
                    <td>{utc(gap.exclusion_end)}</td>
                    <td>{gap.status}</td>
                    <td>{gap.kind}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <h1>Gaps</h1>
        {gaps.error !== null && allGaps.length === 0 ? (
          <div className="banner banner-error">
            <span>{gaps.error}</span>
            <button className="btn" type="button" onClick={() => void refresh()}>
              Retry
            </button>
          </div>
        ) : null}
        {allGaps.length === 0 && gaps.error === null ? (
          <p className="section-note">No gaps recorded.</p>
        ) : null}
        {allGaps.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Series</th>
                  <th>Timeframe</th>
                  <th>Start</th>
                  <th>End</th>
                  <th>Status</th>
                  <th>Kind</th>
                  <th>Source</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {allGaps.map((gap) => (
                  <tr key={gap.gap_id}>
                    <td>{label(gap.instrument_id)}</td>
                    <td>{gap.timeframe}</td>
                    <td>{utc(gap.exclusion_start)}</td>
                    <td>{utc(gap.exclusion_end)}</td>
                    <td>{gap.status}</td>
                    <td>{gap.kind}</td>
                    <td>{gap.source}</td>
                    <td>
                      <button
                        className="btn"
                        type="button"
                        disabled={submitting}
                        onClick={() => void handleRepair(gap.gap_id)}
                      >
                        Repair
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="panel">
        <h1>Actions</h1>
        <div className="btn-row">
          <button
            className="btn"
            type="button"
            disabled={submitting || !seedComplete}
            onClick={() => void handleIngest()}
          >
            Ingest seed universe
          </button>
          <span className="section-note">
            {seedComplete
              ? `${SEED_ASSETS.join(", ")} × ${SEED_TIMEFRAMES.join(", ")} from ${SEED_FROM_ISO.slice(0, 10)} (BINANCE spot; closed bars only)`
              : "Seed instruments (BTC, ETH, SOL) missing from the registry — run make seed-fixtures first"}
          </span>
        </div>
        {actionError !== null ? (
          <div className="banner banner-error" style={{ marginTop: "1rem", marginBottom: 0 }}>
            <span>{actionError}</span>
          </div>
        ) : null}
        {job !== null ? (
          <div style={{ marginTop: "1rem" }}>
            <dl>
              <dt>job</dt>
              <dd>{job.job_id}</dd>
              <dt>kind / status</dt>
              <dd>
                {job.kind} / <strong>{job.status}</strong> ({Math.round(job.progress * 100)}%)
              </dd>
              {job.error !== null ? (
                <>
                  <dt>error</dt>
                  <dd className="bad">{job.error}</dd>
                </>
              ) : null}
              {job.result_ref !== null ? (
                <>
                  <dt>result_ref</dt>
                  <dd>
                    <Link href={`/data/datasets/${job.result_ref}`}>{job.result_ref}</Link>
                  </dd>
                </>
              ) : null}
            </dl>
            {job.logs.length > 0 ? (
              <ul className="log-list">
                {job.logs.map((entry, index) => (
                  <li key={`${index}-${entry}`}>{entry}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </section>
    </>
  );
}
