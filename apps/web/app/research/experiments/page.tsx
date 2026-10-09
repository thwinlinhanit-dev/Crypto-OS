/**
 * T5b.3 - Experiments page + lineage view (spec UI 563).
 *
 * List (status, hypothesis, family, variants, grade summary, frozen badge)
 * and detail: hypothesis, universe, dataset_version (link to manifest),
 * detector@version, resolved_params, selection_method, multiple-testing
 * banner, results by validation level, conclusion, Freeze button (confirm
 * dialog explaining irreversibility), Verify-integrity button, read-only
 * Lineage view (nodes link to stored objects).
 *
 * PD-5: every number rendered here came from a server response; the page
 * never computes statistics. PD-9: validation-level blocks are rendered
 * only from what the API returns (M6/M7 nodes live under
 * `not_implemented` on the lineage payload).
 */

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  freezeExperiment,
  getExperiments,
  getExperimentLineage,
  getFamily,
  getFamilyVariants,
  getHypothesis,
  getStats,
  verifyExperiment,
} from "@cryptosos/client";
import type {
  ExperimentView,
  FamilyView,
  LineageView,
  VariantView,
  VerifyView,
} from "@cryptosos/client";

import { API_BASE } from "@/lib/api";

/** Server-side vocabularies (apps/api/experiments.py), mirrored for display. */
const EXPERIMENT_STATUSES = [
  "IDEA",
  "RUNNING",
  "PROMISING",
  "SUPPORTED",
  "WEAK",
  "REJECTED",
  "VALIDATED",
] as const;

const SELECTION_METHODS = ["PRE_SPECIFIED", "SWEEP_BEST", "MANUAL_CHOICE"] as const;

/** Node kinds the lineage endpoint can return (apps/api/experiments.py). */
type LineageNode = {
  kind: string;
  label: string;
  ref: string | null;
  status: string;
};

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function shortUuid(value: string): string {
  return value.length > 8 ? `${value.slice(0, 8)}…` : value;
}

function utc(iso: string | null): string {
  if (iso == null) return "—";
  return `${iso.slice(0, 19).replace("T", " ")}Z`;
}

function statusTone(status: string): string {
  if (status === "VALIDATED") return "ok";
  if (status === "REJECTED" || status === "WEAK") return "bad";
  if (status === "PROMISING" || status === "SUPPORTED" || status === "RUNNING") return "warn";
  return "muted";
}

/** Multiple-testing banner (spec 8.4 display rule). */
function MultipleTestingBanner({ experiment }: { experiment: ExperimentView }) {
  const familyQuery = useQuery({
    queryKey: ["family", experiment.family_id],
    queryFn: () => getFamily(experiment.family_id, API_BASE),
    staleTime: 60_000,
    retry: 1,
  });
  if (familyQuery.isLoading) return null;
  const family: FamilyView | undefined = familyQuery.data;
  if (family == null) return null;
  const warn =
    family.variant_count > 1 || experiment.selection_method !== "PRE_SPECIFIED";
  if (!warn) return null;
  return (
    <div className="banner banner-stale" role="note">
      <span>
        Multiple-testing warning: {family.variant_count} variants evaluated; this result was
        selected after parameter search.
      </span>
    </div>
  );
}

/** Read-only lineage chain; every node links to its stored object (spec 8.5). */
function LineageViewPanel({ experimentId }: { experimentId: string }) {
  const lineage = useQuery({
    queryKey: ["lineage", experimentId],
    queryFn: () => getExperimentLineage(experimentId, API_BASE),
    staleTime: 30_000,
    retry: 1,
  });

  if (lineage.isLoading) return <p className="muted">Loading lineage.</p>;
  if (lineage.isError) {
    return (
      <div className="banner banner-error">
        <span>Lineage failed: {messageOf(lineage.error)}</span>
        <button type="button" className="btn" onClick={() => void lineage.refetch()}>
          Retry
        </button>
      </div>
    );
  }
  const view: LineageView | undefined = lineage.data;
  if (view == null) return null;

  const nodes = view.nodes as unknown as LineageNode[];
  return (
    <div className="lineage-chain" role="list" aria-label="Experiment lineage">
      {nodes.map((node, index) => {
        const ok = node.status === "OK";
        return (
          <div className="lineage-node" role="listitem" key={`${node.kind}-${index}`}>
            <span className="lineage-kind">{node.kind}</span>
            {node.ref != null ? (
              <Link className="lineage-label" href={node.ref}>
                {node.label}
              </Link>
            ) : (
              <span className="lineage-label">{node.label}</span>
            )}
            <span className={ok ? "ok" : "bad"}>{ok ? "OK" : node.status}</span>
            {index < nodes.length - 1 && <span className="lineage-arrow">→</span>}
          </div>
        );
      })}
      {view.not_implemented.length > 0 && (
        <p className="muted lineage-not-built">
          Not implemented yet: {view.not_implemented.join(", ")} (M6/M7 tables do not exist).
        </p>
      )}
    </div>
  );
}

/** Verify-integrity panel: recompute result hashes -> INTACT/TAMPERED. */
function VerifyPanel({ experimentId }: { experimentId: string }) {
  const verify = useMutation({
    mutationFn: () => verifyExperiment(experimentId, API_BASE),
  });

  if (verify.isPending) return <p className="muted">Verifying…</p>;
  if (verify.isError) {
    return (
      <div className="banner banner-error">
        <span>Verify failed: {messageOf(verify.error)}</span>
        <button type="button" className="btn" onClick={() => verify.reset()}>
          Dismiss
        </button>
      </div>
    );
  }
  const view: VerifyView | undefined = verify.data;
  if (view == null) return null;
  const tampered = view.status !== "INTACT";
  return (
    <div className={tampered ? "banner banner-error" : "banner"} role="status">
      <span>
        Integrity: <strong>{view.status}</strong>
      </span>
    </div>
  );
}

/** Experiment detail: everything spec 563 lists, read-only, plus Freeze. */
function ExperimentDetail({
  experiment,
  onFreezeDone,
}: {
  experiment: ExperimentView;
  onFreezeDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  const freeze = useMutation({
    mutationFn: () => freezeExperiment(experiment.experiment_id, API_BASE),
    onSuccess: () => {
      setConfirming(false);
      void queryClient.invalidateQueries({ queryKey: ["experiments"] });
      onFreezeDone();
    },
  });

  const hypothesis = useQuery({
    queryKey: ["hypothesis", experiment.hypothesis_id],
    queryFn: () => getHypothesis(experiment.hypothesis_id, API_BASE),
    staleTime: 60_000,
    retry: 1,
  });

  const variants = useQuery({
    queryKey: ["family-variants", experiment.family_id],
    queryFn: () => getFamilyVariants(experiment.family_id, API_BASE),
    staleTime: 30_000,
    retry: 1,
  });

  const statsRefs = useMemo(() => {
    const refs = (experiment.refs as Record<string, unknown> | null)?.stats;
    if (!Array.isArray(refs)) return [] as string[];
    return refs.filter((r): r is string => typeof r === "string");
  }, [experiment.refs]);

  const detectorRefs = experiment.detector_refs as Record<string, unknown>;
  const firstDetector = Object.values(detectorRefs)[0] as Record<string, unknown> | undefined;
  const detectorLabel =
    firstDetector != null
      ? `${String(firstDetector.detector_id ?? "detector")}@${String(firstDetector.version ?? "?")}`
      : "—";

  return (
    <div className="experiment-detail">
      <div className="btn-row">
        <Link className="back-link" href="/research/experiments">
          ← All experiments
        </Link>
        {experiment.frozen ? (
          <span className="badge badge-frozen">Frozen</span>
        ) : (
          <button
            type="button"
            className="btn"
            onClick={() => setConfirming(true)}
            disabled={freeze.isPending}
          >
            {freeze.isPending ? "Freezing." : "Freeze experiment"}
          </button>
        )}
      </div>

      {confirming && (
        <div className="banner" role="alertdialog" aria-label="Confirm freeze">
          <span>
            Freezing locks the experiment, its referenced stats results and the hypothesis
            criteria permanently — no UPDATE or DELETE is possible afterwards (spec 8.5).
            Follow-ups create a new experiment with <code>supersedes</code>.
          </span>
          <div className="btn-row">
            <button
              type="button"
              className="btn"
              onClick={() => freeze.mutate()}
              disabled={freeze.isPending}
            >
              {freeze.isPending ? "Freezing." : "Freeze now"}
            </button>
            <button type="button" className="btn" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {freeze.isError && (
        <div className="banner banner-error">
          <span>Freeze failed: {messageOf(freeze.error)}</span>
        </div>
      )}

      <h3>{experiment.title}</h3>
      <p className="muted">
        Status <span className={statusTone(experiment.status)}>{experiment.status}</span> ·
        created {utc(experiment.created_at)}
        {experiment.frozen_at != null ? ` · frozen ${utc(experiment.frozen_at)}` : ""}
      </p>

      <MultipleTestingBanner experiment={experiment} />

      <section aria-label="Provenance">
        <h4>Provenance</h4>
        <dl>
          <dt>hypothesis</dt>
          <dd>
            {hypothesis.isLoading ? (
              "Loading…"
            ) : hypothesis.data != null ? (
              <Link className="lineage-label" href={`/v1/hypotheses/${experiment.hypothesis_id}`}>
                {hypothesis.data.title}
              </Link>
            ) : (
              shortUuid(experiment.hypothesis_id)
            )}
          </dd>
          <dt>dataset_version</dt>
          <dd>
            <Link
              className="lineage-label"
              href={`/data/datasets/${experiment.dataset_version}`}
            >
              {experiment.dataset_version}
            </Link>
          </dd>
          <dt>detector@version</dt>
          <dd>{detectorLabel}</dd>
          <dt>universe</dt>
          <dd>{experiment.universe.map(shortUuid).join(", ")}</dd>
          <dt>timeframe / data_scope</dt>
          <dd>
            {experiment.timeframe} / {experiment.data_scope}
          </dd>
          <dt>selection_method</dt>
          <dd>{experiment.selection_method}</dd>
          <dt>seed</dt>
          <dd>{experiment.seed == null ? "—" : experiment.seed}</dd>
          <dt>engine / code version</dt>
          <dd>
            {experiment.engine_version} / {experiment.code_version}
          </dd>
          <dt>supersedes</dt>
          <dd>{experiment.supersedes == null ? "—" : shortUuid(experiment.supersedes)}</dd>
        </dl>
      </section>

      <section aria-label="Resolved parameters">
        <h4>resolved_params</h4>
        <pre className="json-block">{JSON.stringify(experiment.resolved_params, null, 2)}</pre>
      </section>

      {experiment.conclusion != null && (
        <section aria-label="Conclusion">
          <h4>Conclusion</h4>
          <p>{experiment.conclusion}</p>
        </section>
      )}

      <section aria-label="Results">
        <h4>Results ({statsRefs.length})</h4>
        {statsRefs.length === 0 ? (
          <p className="muted">No stats results referenced yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>ref_id</th>
                  <th>scope</th>
                  <th>grade</th>
                  <th>n_eff</th>
                  <th>dataset</th>
                </tr>
              </thead>
              <tbody>
                {statsRefs.map((ref) => (
                  <StatsResultRow key={ref} refId={ref} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-label="Family variants">
        <h4>Family variants</h4>
        {variants.isLoading ? (
          <p className="muted">Loading variants.</p>
        ) : variants.isError ? (
          <p className="muted">Variants unavailable: {messageOf(variants.error)}</p>
        ) : (variants.data ?? []).length === 0 ? (
          <p className="muted">None recorded.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>variant</th>
                  <th>result ref</th>
                  <th>selected</th>
                  <th>created</th>
                </tr>
              </thead>
              <tbody>
                {(variants.data as VariantView[]).map((variant) => (
                  <tr key={variant.variant_id}>
                    <td className="hash">{shortUuid(variant.variant_id)}</td>
                    <td>
                      <Link className="lineage-label" href={`/v1/stats/${variant.result_ref}`}>
                        {shortUuid(variant.result_ref)}
                      </Link>
                    </td>
                    <td>{variant.selected ? "yes" : "no"}</td>
                    <td>{utc(variant.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-label="Integrity">
        <h4>Integrity</h4>
        {experiment.frozen ? (
          <VerifyPanel experimentId={experiment.experiment_id} />
        ) : (
          <p className="muted">Freeze the experiment to verify its stored hashes.</p>
        )}
      </section>

      <section aria-label="Lineage">
        <h4>Lineage</h4>
        <LineageViewPanel experimentId={experiment.experiment_id} />
      </section>
    </div>
  );
}

/** One row of the results table: grade/n_eff/scope from the stored result. */
function StatsResultRow({ refId }: { refId: string }) {
  const stats = useQuery({
    queryKey: ["stats-result", refId],
    queryFn: () => getStats(refId, API_BASE),
    staleTime: 60_000,
    retry: 1,
  });
  if (stats.isLoading) {
    return (
      <tr>
        <td colSpan={5}>Loading…</td>
      </tr>
    );
  }
  if (stats.isError || stats.data == null) {
    return (
      <tr>
        <td colSpan={5} className="bad">
          Unavailable: {messageOf(stats.error ?? "not found")}
        </td>
      </tr>
    );
  }
  const result = stats.data.result as Record<string, unknown> | undefined;
  return (
    <tr>
      <td className="hash">{shortUuid(refId)}</td>
      <td>{stats.data.data_scope}</td>
      <td className={statusTone(String(result?.grade ?? "n/a"))}>
        {String(result?.grade ?? "n/a")}
      </td>
      <td className="numeric">{String(result?.n_eff ?? "—")}</td>
      <td className="hash">{stats.data.dataset_version}</td>
    </tr>
  );
}

/**
 * Experiments list + detail in one route. Selecting a row loads the detail
 * pane (spec UI 563: "list … and detail: …"). Freeze/verify are the only
 * write actions; everything else is read-only.
 */
export default function ExperimentsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const experiments = useQuery({
    queryKey: ["experiments"],
    queryFn: () => getExperiments(API_BASE),
    staleTime: 30_000,
    retry: 1,
  });

  const rows = experiments.data ?? [];
  const selected = useMemo(
    () => rows.find((row) => row.experiment_id === selectedId) ?? null,
    [rows, selectedId],
  );

  const onFreezeDone = useCallback(() => {
    void experiments.refetch();
  }, [experiments]);

  return (
    <div className="panel">
      <h2>Experiments</h2>
      <p className="muted">
        Every reproducible attempt, its family, and its lineage. Frozen experiments are
        immutable — freeze locks referenced results, the hypothesis criteria and the
        experiment row itself (spec 8.5).
      </p>

      {experiments.isLoading ? (
        <p className="muted">Loading experiments.</p>
      ) : experiments.isError ? (
        <div className="banner banner-error">
          <span>Failed: {messageOf(experiments.error)}</span>
          <button type="button" className="btn" onClick={() => void experiments.refetch()}>
            Retry
          </button>
        </div>
      ) : rows.length === 0 ? (
        <p className="muted">
          No experiments yet. Create one from a hypothesis after running stats — the API mints
          a singleton family when you omit <code>family_id</code>.
        </p>
      ) : (
        <div className="experiments-grid">
          <section aria-label="Experiment list">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>title</th>
                    <th>status</th>
                    <th>family</th>
                    <th>frozen</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr
                      key={row.experiment_id}
                      className={row.experiment_id === selectedId ? "row-selected" : undefined}
                    >
                      <td>
                        <button
                          type="button"
                          className="btn link"
                          onClick={() =>
                            setSelectedId(
                              row.experiment_id === selectedId ? null : row.experiment_id,
                            )
                          }
                        >
                          {row.title}
                        </button>
                      </td>
                      <td className={statusTone(row.status)}>{row.status}</td>
                      <td className="hash">{shortUuid(row.family_id)}</td>
                      <td>{row.frozen ? "yes" : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {selected && (
            <section aria-label="Experiment detail">
              <ExperimentDetail experiment={selected} onFreezeDone={onFreezeDone} />
            </section>
          )}
        </div>
      )}
    </div>
  );
}
