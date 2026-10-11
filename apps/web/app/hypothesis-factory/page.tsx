/**
 * Phase 2 — the Hypothesis Factory manual research workflow (spec §24 Phase 2 AC):
 * research-question entry (verbatim preservation) → hypothesis editor →
 * definition validation → detail view. No AI provider required.
 *
 * PD-5: every number and status rendered here came from a server response;
 * the page never computes statistics or decides a status. PD-9: the
 * evidence-ladder blocks render only what the API returns.
 */

"use client";

import { useCallback, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createHFHypothesis,
  getHFHypothesis,
  getHFVocabularies,
  validateHFDefinition,
  findHFDuplicates,
} from "@cryptosos/client";
import type { HFHypothesisView } from "@cryptosos/client";

import { API_BASE } from "@/lib/api";

/** The spec §6 formal-definition fields the editor captures. */
const REQUIRED_FIELDS = [
  "null_hypothesis",
  "alternative_hypothesis",
  "independent_vars",
  "dependent_vars",
  "features",
  "population",
  "timeframe",
  "observation_ts_rule",
  "outcome_horizon",
  "expected_direction",
  "data_requirements",
  "proposed_statistical_method",
  "falsification_criteria",
] as const;

type FieldName = (typeof REQUIRED_FIELDS)[number];

const ARRAY_FIELDS = new Set<FieldName>([
  "independent_vars",
  "dependent_vars",
  "features",
  "data_requirements",
  "falsification_criteria",
]);

function emptySpec(): Record<string, unknown> {
  return {
    null_hypothesis: "",
    alternative_hypothesis: "",
    independent_vars: [] as string[],
    dependent_vars: [] as string[],
    features: [] as string[],
    population: "",
    timeframe: "1D",
    observation_ts_rule: "",
    outcome_horizon: "5 bars",
    expected_direction: "HIGHER",
    data_requirements: [] as string[],
    proposed_statistical_method: "block-bootstrap mean difference",
    falsification_criteria: [] as string[],
    economic_significance_threshold: null,
  };
}

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function shortUuid(value: string): string {
  return value.length > 8 ? `${value.slice(0, 8)}…` : value;
}

/** The spec §14.2 ladder order (rendered top-down; the server owns it). */
const LADDER = [
  "NOT_TESTED",
  "EXPLORATORY",
  "NEEDS_MORE_DATA",
  "INVALID_EXPERIMENT",
  "FAILED_STATISTICAL_TEST",
  "FAILED_ROBUSTNESS",
  "FAILED_OUT_OF_SAMPLE",
  "PASSED_RESEARCH_VALIDATION",
  "PAPER_TRADING",
  "PAPER_TRADING_PASSED",
  "REQUIRES_HUMAN_REVIEW",
  "ARCHIVED",
] as const;

function ArrayField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <textarea
        rows={3}
        value={value.join("\n")}
        onChange={(event) =>
          onChange(
            event.target.value
              .split("\n")
              .map((line) => line.trim())
              .filter((line) => line.length > 0),
          )
        }
      />
    </label>
  );
}

export default function HypothesisFactoryPage() {
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState("");
  const [title, setTitle] = useState("");
  const [domain, setDomain] = useState("DERIVATIVES");
  const [origin, setOrigin] = useState("HUMAN");
  const [spec, setSpec] = useState<Record<string, unknown>>(emptySpec);
  const [created, setCreated] = useState<HFHypothesisView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duplicates, setDuplicates] = useState<
    { hypothesis_id: string; title: string; status: string; match: string }[]
  >([]);

  const vocabularies = useQuery({
    queryKey: ["hf-vocabularies"],
    queryFn: () => getHFVocabularies(API_BASE),
  });

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["hf-vocabularies"] });
  }, [queryClient]);

  const create = useMutation({
    mutationFn: () =>
      createHFHypothesis(
        {
          title,
          domain,
          origin,
          spec,
          original_question: question.trim() || null,
        },
        API_BASE,
      ),
    onSuccess: (view) => {
      setCreated(view);
      setError(null);
      invalidate();
    },
    onError: (cause) => setError(messageOf(cause)),
  });

  const setField = (name: string, value: unknown) =>
    setSpec((current) => ({ ...current, [name]: value }));

  const checkDuplicates = useMutation({
    mutationFn: () => findHFDuplicates(spec, API_BASE),
    onSuccess: (result) => {
      setDuplicates(result.matches);
      setError(null);
    },
    onError: (cause) => setError(messageOf(cause)),
  });

  const domains = vocabularies.data?.domains ?? [
    "PRICE_TREND",
    "DERIVATIVES",
    "CAPITULATION_RECOVERY",
    "CROSS_ASSET",
    "ON_CHAIN",
    "FUNDAMENTAL",
    "NARRATIVE",
    "REGIME",
    "NEGATIVE",
  ];
  const origins = vocabularies.data?.origins ?? [
    "AI_GENERATED",
    "PATTERN_DISCOVERY",
    "RESEARCH_GAP",
    "HUMAN",
  ];

  return (
    <main className="page">
      <header className="page-head">
        <h1>Hypothesis Factory</h1>
        <p className="page-sub">
          The manual research workflow (spec §24 Phase 2). Every hypothesis is
          falsifiable, versioned, and audited. The evidence ladder is owned by
          the rules — an AI proposal is a proposal, never a finding.
        </p>
      </header>

      <section className="panel">
        <h2>1 · Research question</h2>
        <p className="panel-sub">
          Recorded verbatim (spec §2.2: the question is preserved alongside the
          hypothesis).
        </p>
        <label className="field">
          <span className="field-label">Research question</span>
          <textarea
            rows={2}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Does extreme open-interest contraction improve capitulation-reclaim signals?"
          />
        </label>
      </section>

      <section className="panel">
        <h2>2 · Hypothesis definition</h2>
        <p className="panel-sub">
          The formal spec §6 fields. An underspecified hypothesis is rejected
          before it is stored (PD-10).
        </p>
        <div className="grid-2">
          <label className="field">
            <span className="field-label">Title</span>
            <input
              type="text"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">Domain</span>
            <select value={domain} onChange={(event) => setDomain(event.target.value)}>
              {domains.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Origin</span>
            <select value={origin} onChange={(event) => setOrigin(event.target.value)}>
              {origins.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Timeframe</span>
            <select
              value={String(spec.timeframe ?? "1D")}
              onChange={(event) => setField("timeframe", event.target.value)}
            >
              <option value="1D">1D</option>
              <option value="4H">4H</option>
              <option value="1H">1H</option>
            </select>
          </label>
        </div>

        <label className="field">
          <span className="field-label">Null hypothesis</span>
          <textarea
            rows={2}
            value={String(spec.null_hypothesis ?? "")}
            onChange={(event) => setField("null_hypothesis", event.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Alternative hypothesis</span>
          <textarea
            rows={2}
            value={String(spec.alternative_hypothesis ?? "")}
            onChange={(event) => setField("alternative_hypothesis", event.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Observation timestamp rule</span>
          <input
            type="text"
            value={String(spec.observation_ts_rule ?? "")}
            onChange={(event) => setField("observation_ts_rule", event.target.value)}
            placeholder="bar close_time of the reclaim bar"
          />
          <span className="field-hint">
            Must anchor on a closed bar (PD-2); a future-referencing rule is
            rejected.
          </span>
        </label>
        <label className="field">
          <span className="field-label">Population</span>
          <input
            type="text"
            value={String(spec.population ?? "")}
            onChange={(event) => setField("population", event.target.value)}
            placeholder="BTC, ETH daily 2024-2026"
          />
        </label>
        <div className="grid-2">
          <label className="field">
            <span className="field-label">Outcome horizon</span>
            <input
              type="text"
              value={String(spec.outcome_horizon ?? "")}
              onChange={(event) => setField("outcome_horizon", event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">Expected direction</span>
            <select
              value={String(spec.expected_direction ?? "HIGHER")}
              onChange={(event) => setField("expected_direction", event.target.value)}
            >
              <option value="HIGHER">HIGHER</option>
              <option value="LOWER">LOWER</option>
              <option value="DIFFERENT">DIFFERENT</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">Statistical method</span>
            <select
              value={String(spec.proposed_statistical_method ?? "")}
              onChange={(event) =>
                setField("proposed_statistical_method", event.target.value)
              }
            >
              <option value="block-bootstrap mean difference">
                block-bootstrap mean difference
              </option>
              <option value="cluster-bootstrap mean difference">
                cluster-bootstrap mean difference
              </option>
              <option value="wilson win-rate test">wilson win-rate test</option>
              <option value="event study (standardized)">
                event study (standardized)
              </option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">Economic-significance threshold</span>
            <input
              type="number"
              step="any"
              value={
                spec.economic_significance_threshold == null
                  ? ""
                  : String(spec.economic_significance_threshold)
              }
              onChange={(event) =>
                setField(
                  "economic_significance_threshold",
                  event.target.value === "" ? null : Number(event.target.value),
                )
              }
            />
          </label>
        </div>

        <ArrayField
          label="Independent variables (one per line)"
          value={(spec.independent_vars as string[]) ?? []}
          onChange={(next) => setField("independent_vars", next)}
        />
        <ArrayField
          label="Dependent variables (one per line)"
          value={(spec.dependent_vars as string[]) ?? []}
          onChange={(next) => setField("dependent_vars", next)}
        />
        <ArrayField
          label="Features (one per line)"
          value={(spec.features as string[]) ?? []}
          onChange={(next) => setField("features", next)}
        />
        <ArrayField
          label="Data requirements (one per line)"
          value={(spec.data_requirements as string[]) ?? []}
          onChange={(next) => setField("data_requirements", next)}
        />
        <ArrayField
          label="Falsification criteria (one per line)"
          value={(spec.falsification_criteria as string[]) ?? []}
          onChange={(next) => setField("falsification_criteria", next)}
        />

        <div className="row">
          <button
            type="button"
            className="btn"
            onClick={() => create.mutate()}
            disabled={create.isPending || !title.trim()}
          >
            {create.isPending ? "Storing…" : "Store hypothesis"}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => checkDuplicates.mutate()}
            disabled={checkDuplicates.isPending}
          >
            Check duplicates
          </button>
        </div>
        {error ? <p className="alert alert-error">{error}</p> : null}
      </section>

      {duplicates.length > 0 ? (
        <section className="panel">
          <h2>Duplicate review</h2>
          <p className="panel-sub">
            The deterministic exact-dup search (spec §8 level 1) — not an AI
            judgement.
          </p>
          <ul className="list">
            {duplicates.map((match) => (
              <li key={match.hypothesis_id} className="list-row">
                <strong>{match.title}</strong>{" "}
                <span className="muted">
                  {shortUuid(match.hypothesis_id)} · {match.status} · {match.match}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {created ? (
        <section className="panel">
          <h2>3 · Stored hypothesis</h2>
          <p className="panel-sub">
            Stored as <strong>{created.origin}</strong> at{" "}
            <strong>{created.status}</strong> (spec §17.1: an AI proposal is a
            proposal, never a finding).
          </p>
          <dl>
            <dt>Hypothesis</dt>
            <dd>{shortUuid(created.hypothesis_id)}</dd>
            <dt>Title</dt>
            <dd>{created.title}</dd>
            <dt>Domain</dt>
            <dd>{created.domain}</dd>
            <dt>Version</dt>
            <dd>{created.version ?? 1}</dd>
            <dt>Research question</dt>
            <dd>{created.original_question ?? "—"}</dd>
          </dl>
          <h3 className="mt">Evidence ladder</h3>
          <p className="panel-sub">
            The §14.2 states, in order. The status is owned by the explicit
            transition rules — the UI only renders it.
          </p>
          <ol className="ladder">
            {LADDER.map((status) => (
              <li
                key={status}
                className={status === created.status ? "ladder-item current" : "ladder-item"}
              >
                {status}
              </li>
            ))}
          </ol>
          {created.spec ? (
            <details className="mt">
              <summary>Stored definition (version {created.version ?? 1})</summary>
              <pre className="code-block">
                {JSON.stringify(created.spec, null, 2)}
              </pre>
            </details>
          ) : null}
        </section>
      ) : null}
    </main>
  );
}
