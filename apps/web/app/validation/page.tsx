/**
 * T7.4 — Validation dashboard (spec 9.3/9.4, UI).
 *
 * Backtest picker, the three levels as expandable panels with the status
 * word + numeric basis (the state word is always the text; colour never
 * alone — the M2 badge contract), the baseline distribution render, and
 * the overall status list (9.4: a list, never a composite).
 *
 * PD-5: every number comes from a stored server result.
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getBacktests,
  getValidation,
  postRandomBaseline,
  postValidation,
} from "@cryptosos/client";
import type { BacktestView, RunView } from "@cryptosos/client";

import { API_BASE } from "@/lib/api";

/** 9.3 kinds the dashboard offers (Level 2 statistical + Level 3 economic). */
const LEVEL2_KINDS = ["edge", "oos", "multiple_testing"];
const LEVEL3_KINDS = ["gap_risk", "monte_carlo"];

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function statusTone(status: string): string {
  if (status === "PASS" || status === "VALIDATED") return "ok";
  if (status === "FAIL" || status === "REJECTED") return "bad";
  if (status === "WARN" || status === "PROMISING") return "warn";
  return "muted";
}

/** One level panel: the status word + the numeric basis (9.3). */
function RunPanel({ run }: { run: RunView }) {
  const result = run.result as Record<string, unknown>;
  const basis = (result.basis as Record<string, unknown>) ?? {};
  return (
    <div className="lineage-node">
      <span className="lineage-kind">
        L{run.level} · {run.kind}
      </span>
      <span className={statusTone(run.status)}>{run.status}</span>
      {run.locked && <span className="badge badge-frozen">locked</span>}
      <pre className="json-block" style={{ flex: 1, margin: 0 }}>
        {JSON.stringify(basis, null, 2)}
      </pre>
    </div>
  );
}

export default function ValidationPage() {
  const queryClient = useQueryClient();
  const [backtestId, setBacktestId] = useState("");
  const [runIds, setRunIds] = useState<string[]>([]);
  const [baselineRunId, setBaselineRunId] = useState<string | null>(null);

  const backtests = useQuery({
    queryKey: ["backtests-validation"],
    queryFn: () => getBacktests(API_BASE),
    staleTime: 15_000,
    retry: 1,
  });
  const backtestRows = (backtests.data ?? []) as BacktestView[];

  useEffect(() => {
    if (backtestId === "" && backtestRows.length > 0) setBacktestId(backtestRows[0].backtest_id);
  }, [backtestId, backtestRows]);

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["backtests-validation"] });
  }, [queryClient]);

  const runLevels = useMutation({
    mutationFn: () =>
      postValidation(
        { backtest_id: backtestId, kinds: [...LEVEL2_KINDS, ...LEVEL3_KINDS] },
        API_BASE,
      ),
    onSuccess: (views) => {
      setRunIds(views.map((view) => view.run_id));
      refresh();
    },
  });

  const runBaseline = useMutation({
    mutationFn: () => postRandomBaseline({ backtest_id: backtestId, seeds: 1000 }, API_BASE),
    onSuccess: (view) => setBaselineRunId(view.run_id),
  });

  const firstRun = useQuery({
    queryKey: ["validation-run", runIds[0]],
    queryFn: () => getValidation(runIds[0] ?? "", API_BASE),
    enabled: runIds.length > 0,
    staleTime: 10_000,
    retry: 1,
  });

  const baselineRun = useQuery({
    queryKey: ["validation-run", baselineRunId],
    queryFn: () => getValidation(baselineRunId ?? "", API_BASE),
    enabled: baselineRunId != null,
    staleTime: 30_000,
    retry: 1,
  });

  const firstResult = firstRun.data?.result as Record<string, unknown> | undefined;
  const overall = (firstResult?.overall as Record<string, unknown>) ?? null;
  const baselineResult = baselineRun.data?.result as Record<string, unknown> | undefined;
  const percentiles = (baselineResult?.percentiles as Record<string, Record<string, number>>) ?? null;

  return (
    <div className="panel">
      <h2>Validation</h2>
      <p className="muted">
        The three levels of spec 9.3 against a stored backtest. Every test returns a status with
        its numeric basis; the overall status is a list, never a composite score (9.4).
      </p>

      <div className="btn-row" role="group" aria-label="Validation controls">
        <select
          className="chart-select"
          value={backtestId}
          onChange={(event) => setBacktestId(event.target.value)}
          aria-label="Backtest"
        >
          <option value="">Select backtest</option>
          {backtestRows.map((row) => (
            <option key={row.backtest_id} value={row.backtest_id}>
              {row.strategy_id} @ {row.version} · {row.status}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn"
          disabled={backtestId === "" || runLevels.isPending}
          onClick={() => runLevels.mutate()}
        >
          {runLevels.isPending ? "Running." : "Run levels"}
        </button>
        <button
          type="button"
          className="btn"
          disabled={backtestId === "" || runBaseline.isPending}
          onClick={() => runBaseline.mutate()}
        >
          {runBaseline.isPending ? "Sampling." : "Random baseline (1000 seeds)"}
        </button>
      </div>
      {runLevels.isError && (
        <div className="banner banner-error">
          <span>Validation failed: {messageOf(runLevels.error)}</span>
        </div>
      )}
      {runBaseline.isError && (
        <div className="banner banner-error">
          <span>Baseline failed: {messageOf(runBaseline.error)}</span>
        </div>
      )}

      {overall != null && (
        <section aria-label="Overall status">
          <h4>Overall status (9.4 — a list, never a composite)</h4>
          <div className={`banner ${overall.status === "VALIDATED" ? "" : "banner-stale"}`} role="status">
            <strong className={statusTone(String(overall.status))}>{String(overall.status)}</strong>
            {typeof overall.reason === "string" && <span>{overall.reason}</span>}
            {Array.isArray(overall.unmet) && (
              <ul className="log-list">
                {(overall.unmet as string[]).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      {firstRun.data != null && (
        <section aria-label="Levels">
          <h4>Levels</h4>
          <RunPanel run={firstRun.data} />
          {runIds.slice(1).map((id) => (
            <LevelRunRow key={id} runId={id} />
          ))}
        </section>
      )}

      {percentiles != null && (
        <section aria-label="Baseline distribution">
          <h4>Random-entry baseline distribution (9.2)</h4>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>metric</th>
                  <th>P5</th>
                  <th>P50</th>
                  <th>P80</th>
                  <th>P95</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(percentiles).map(([metric, values]) => (
                  <tr key={metric}>
                    <td>{metric}</td>
                    <td className="numeric">{values.p5?.toFixed(3) ?? "-"}</td>
                    <td className="numeric">{values.p50?.toFixed(3) ?? "-"}</td>
                    <td className="numeric">{values.p80?.toFixed(3) ?? "-"}</td>
                    <td className="numeric">{values.p95?.toFixed(3) ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted">
            strategy percentile:{" "}
            {baselineResult?.strategy_percentile == null
              ? "n/a (full per-seed simulator runs land with the M7 suite)"
              : String(baselineResult.strategy_percentile)}
          </p>
        </section>
      )}

      <p className="muted">
        Backtests live in the{" "}
        <Link className="lineage-label" href="/strategy">
          Strategy Lab
        </Link>
        .
      </p>
    </div>
  );
}

/** One queued/ran level run, fetched by id. */
function LevelRunRow({ runId }: { runId: string }) {
  const run = useQuery({
    queryKey: ["validation-run", runId],
    queryFn: () => getValidation(runId, API_BASE),
    staleTime: 10_000,
    retry: 1,
  });
  if (run.isLoading) return <p className="muted">Loading {runId}…</p>;
  if (run.data == null) return null;
  return <RunPanel run={run.data} />;
}
