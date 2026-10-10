/**
 * T6.4 — Strategy Lab (spec UI 556/563): strategy selector, backtest run,
 * trades/equity/metrics, and the trade overlay data.
 *
 * PD-5: every number rendered here came from a server response; the page
 * never computes statistics. The trade overlay itself (chart markers) lands
 * with the chart integration; this page carries the results views.
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getBacktest,
  getBacktestEquity,
  getBacktestMetrics,
  getBacktestSkipped,
  getBacktestTrades,
  getBacktests,
  getExperiments,
  getStrategies,
  postBacktest,
} from "@cryptosos/client";
import type {
  BacktestView,
  ExperimentView,
  StrategyView,
  TradeView,
} from "@cryptosos/client";

import { API_BASE } from "@/lib/api";

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function shortUuid(value: string): string {
  return value.length > 8 ? `${value.slice(0, 8)}…` : value;
}

function fmt(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "-";
  return value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function pct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function statusTone(status: string): string {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "bad";
  return "warn";
}

/** Trades table (7.5 record; spec UI "results by validation level" - M7). */
function TradesTable({ backtestId }: { backtestId: string }) {
  const trades = useQuery({
    queryKey: ["backtest-trades", backtestId],
    queryFn: () => getBacktestTrades(backtestId, API_BASE),
    staleTime: 30_000,
    retry: 1,
  });
  if (trades.isLoading) return <p className="muted">Loading trades.</p>;
  if (trades.isError) {
    return (
      <div className="banner banner-error">
        <span>Failed: {messageOf(trades.error)}</span>
        <button type="button" className="btn" onClick={() => void trades.refetch()}>
          Retry
        </button>
      </div>
    );
  }
  const rows = (trades.data ?? []) as TradeView[];
  if (rows.length === 0) return <p className="muted">No trades yet.</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>instrument</th>
            <th>entry</th>
            <th>exit</th>
            <th>reason</th>
            <th>R</th>
            <th>pnl</th>
            <th>bars</th>
            <th>gap</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((trade) => (
            <tr key={`${trade.backtest_id}-${trade.trade_no}`}>
              <td className="hash">{shortUuid(trade.instrument_id)}</td>
              <td className="numeric">{fmt(trade.entry_price)}</td>
              <td className="numeric">
                {trade.exit_price == null ? "open" : fmt(trade.exit_price)}
              </td>
              <td>{trade.exit_reason ?? "OPEN"}</td>
              <td className="numeric">{fmt(trade.r_multiple)}</td>
              <td className="numeric">{fmt(trade.pnl)}</td>
              <td className="numeric">{trade.bars_held ?? "-"}</td>
              <td>{trade.data_gap_during_hold ? "yes" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Skipped/not-filled signals (7.3.1 - M6 AC: no signal disappears). */
function SkippedTable({ backtestId }: { backtestId: string }) {
  const skipped = useQuery({
    queryKey: ["backtest-skipped", backtestId],
    queryFn: () => getBacktestSkipped(backtestId, API_BASE),
    staleTime: 30_000,
    retry: 1,
  });
  if (skipped.isLoading) return <p className="muted">Loading skipped signals.</p>;
  const rows = (skipped.data ?? []) as Record<string, unknown>[];
  if (rows.length === 0) return <p className="muted">None - every signal was recorded.</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>instrument</th>
            <th>reason</th>
            <th>signal time</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              <td className="hash">{shortUuid(String(row.instrument ?? ""))}</td>
              <td>{String(row.reason ?? row.outcome ?? "-")}</td>
              <td>{String(row.signal_time ?? "-")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Metrics summary block (the M7 validation suite computes the rest). */
function MetricsPanel({ backtestId }: { backtestId: string }) {
  const metrics = useQuery({
    queryKey: ["backtest-metrics", backtestId],
    queryFn: () => getBacktestMetrics(backtestId, API_BASE),
    staleTime: 30_000,
    retry: 1,
  });
  if (metrics.isLoading) return <p className="muted">Loading metrics.</p>;
  const data = (metrics.data ?? {}) as Record<string, unknown>;
  return (
    <dl>
      <dt>trades</dt>
      <dd>{String(data.trade_count ?? "-")}</dd>
      <dt>closed</dt>
      <dd>{String(data.closed_count ?? "-")}</dd>
      <dt>win rate</dt>
      <dd>{pct(data.win_rate as number | null)}</dd>
      <dt>total pnl</dt>
      <dd>{fmt(data.total_pnl as number | null)}</dd>
      <dt>avg R</dt>
      <dd>{fmt(data.avg_r as number | null)}</dd>
      <dt>final equity</dt>
      <dd>{fmt(data.final_equity as number | null)}</dd>
      <dt>flagged</dt>
      <dd>{String(data.flagged_count ?? "-")}</dd>
    </dl>
  );
}

/** Equity curve: a compact sparkline built from the stored per-bar values. */
function EquityCurve({ backtestId }: { backtestId: string }) {
  const equity = useQuery({
    queryKey: ["backtest-equity", backtestId],
    queryFn: () => getBacktestEquity(backtestId, API_BASE),
    staleTime: 30_000,
    retry: 1,
  });
  if (equity.isLoading) return <p className="muted">Loading equity curve.</p>;
  const points = (equity.data ?? []) as Record<string, unknown>[];
  if (points.length === 0) return <p className="muted">No equity curve.</p>;
  const values = points.map((point) => Number(point.equity ?? 0));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return (
    <div className="equity-spark" role="img" aria-label="Equity curve">
      {values.map((value, index) => (
        <div
          key={index}
          className="equity-spark-bar"
          style={{ height: `${Math.max(2, Math.round(((value - min) / span) * 60))}px` }}
          title={`${points[index].time}: ${value.toFixed(2)}`}
        />
      ))}
    </div>
  );
}

export default function StrategyLabPage() {
  const queryClient = useQueryClient();
  const [strategyId, setStrategyId] = useState("");
  const [version, setVersion] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [fillModel, setFillModel] = useState("REALISTIC_OHLC");
  const [portfolio, setPortfolio] = useState(false);
  const [selectedBacktest, setSelectedBacktest] = useState<string | null>(null);

  const strategies = useQuery({
    queryKey: ["strategies"],
    queryFn: () => getStrategies(API_BASE),
    staleTime: 30_000,
    retry: 1,
  });
  const experiments = useQuery({
    queryKey: ["experiments-lab"],
    queryFn: () => getExperiments(API_BASE),
    staleTime: 30_000,
    retry: 1,
  });
  const backtests = useQuery({
    queryKey: ["backtests"],
    queryFn: () => getBacktests(API_BASE),
    staleTime: 15_000,
    retry: 1,
  });

  const strategyRows = (strategies.data ?? []) as StrategyView[];
  const experimentRows = (experiments.data ?? []) as ExperimentView[];
  const backtestRows = (backtests.data ?? []) as BacktestView[];

  useEffect(() => {
    if (strategyId === "" && strategyRows.length > 0) {
      setStrategyId(strategyRows[0].strategy_id);
      setVersion(strategyRows[0].version);
    }
  }, [strategyId, strategyRows]);

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["backtests"] });
  }, [queryClient]);

  const run = useMutation({
    mutationFn: () =>
      postBacktest(
        {
          experiment_id: experimentId,
          strategy_id: strategyId,
          version,
          universe: [experimentRows[0]?.universe[0] ?? "00000000-0000-0000-0000-000000000000"],
          timeframe: "1D",
          fill_model: fillModel,
          data_scope: "DEVELOPMENT",
          portfolio,
          per_asset_cap_pct: 25.0,
        },
        API_BASE,
      ),
    onSuccess: (view) => {
      setSelectedBacktest(view.backtest_id);
      refresh();
    },
  });

  const detail = useQuery({
    queryKey: ["backtest", selectedBacktest],
    queryFn: () => getBacktest(selectedBacktest ?? "", API_BASE),
    enabled: selectedBacktest != null,
    staleTime: 10_000,
    retry: 1,
  });

  const canRun =
    strategyId !== "" && version !== "" && experimentId !== "" && !run.isPending;

  return (
    <div className="panel">
      <h2>Strategy Lab</h2>
      <p className="muted">
        Run a versioned strategy against an experiment, then read its trades, skipped signals,
        metrics and equity curve. Every backtest belongs to an experiment (D-041); frozen
        experiments stay immutable (spec 8.5).
      </p>

      <div className="btn-row" role="group" aria-label="Backtest controls">
        <select
          className="chart-select"
          value={strategyId}
          onChange={(event) => {
            const chosen = strategyRows.find((row) => row.strategy_id === event.target.value);
            setStrategyId(event.target.value);
            setVersion(chosen?.version ?? "");
          }}
          aria-label="Strategy"
        >
          <option value="">Select strategy</option>
          {strategyRows.map((row) => (
            <option key={`${row.strategy_id}-${row.version}`} value={row.strategy_id}>
              {row.strategy_id} @ {row.version}
            </option>
          ))}
        </select>
        <select
          className="chart-select"
          value={experimentId}
          onChange={(event) => setExperimentId(event.target.value)}
          aria-label="Experiment"
        >
          <option value="">Select experiment</option>
          {experimentRows.map((row) => (
            <option key={row.experiment_id} value={row.experiment_id}>
              {row.title}
            </option>
          ))}
        </select>
        <select
          className="chart-select"
          value={fillModel}
          onChange={(event) => setFillModel(event.target.value)}
          aria-label="Fill model"
        >
          <option value="REALISTIC_OHLC">REALISTIC_OHLC</option>
          <option value="PARITY">PARITY</option>
        </select>
        <label className="btn link">
          <input
            type="checkbox"
            checked={portfolio}
            onChange={(event) => setPortfolio(event.target.checked)}
          />{" "}
          portfolio mode
        </label>
        <button type="button" className="btn" disabled={!canRun} onClick={() => run.mutate()}>
          {run.isPending ? "Running." : "Run backtest"}
        </button>
      </div>
      {run.isError && (
        <div className="banner banner-error">
          <span>Backtest failed: {messageOf(run.error)}</span>
        </div>
      )}

      <section aria-label="Backtests">
        <h4>Backtests</h4>
        {backtests.isLoading ? (
          <p className="muted">Loading backtests.</p>
        ) : backtests.isError ? (
          <p className="muted">Unavailable: {messageOf(backtests.error)}</p>
        ) : backtestRows.length === 0 ? (
          <p className="muted">No backtests yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>strategy</th>
                  <th>status</th>
                  <th>fill model</th>
                  <th>scope</th>
                  <th>locked</th>
                </tr>
              </thead>
              <tbody>
                {backtestRows.map((row) => (
                  <tr
                    key={row.backtest_id}
                    className={row.backtest_id === selectedBacktest ? "row-selected" : undefined}
                  >
                    <td>
                      <button
                        type="button"
                        className="btn link"
                        onClick={() =>
                          setSelectedBacktest(
                            row.backtest_id === selectedBacktest ? null : row.backtest_id,
                          )
                        }
                      >
                        {row.strategy_id} @ {row.version}
                      </button>
                    </td>
                    <td className={statusTone(row.status)}>{row.status}</td>
                    <td>{row.fill_model}</td>
                    <td>{row.data_scope}</td>
                    <td>{row.locked ? "yes" : "no"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selectedBacktest && detail.data && (
        <section aria-label="Backtest detail">
          <h4>
            {detail.data.strategy_id} @ {detail.data.version} · {detail.data.status}
          </h4>
          <p className="muted">
            dataset{" "}
            <Link className="lineage-label" href={`/data/datasets/${detail.data.dataset_version}`}>
              {detail.data.dataset_version}
            </Link>{" "}
            · {detail.data.fill_model} · {detail.data.data_scope} · seed{" "}
            {detail.data.seed ?? "none"}
          </p>
          <MetricsPanel backtestId={selectedBacktest} />
          <h4>Equity curve</h4>
          <EquityCurve backtestId={selectedBacktest} />
          <h4>Trades</h4>
          <TradesTable backtestId={selectedBacktest} />
          <h4>Skipped signals</h4>
          <SkippedTable backtestId={selectedBacktest} />
        </section>
      )}
    </div>
  );
}
