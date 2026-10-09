/**
 * M5 - the ONLY renderer of Evidence Grade wording in the web app (design
 * note m5_stats section 10; enforced by tests/test_grade_wording.py).
 *
 * Always shows grade + n_eff + interval + data_scope together (spec 8.3);
 * never a bare point estimate or win rate. The `grade`, `nEff`, `ci` and
 * `dataScope` props are a required tuple so the AC triple is enforced by
 * construction: no caller can render a grade without its evidence.
 */

export interface EvidenceGradeBadgeProps {
  /** "A" | "B" | "C" | "D" | null (null = HOLDOUT scope, ungraded). */
  grade: string | null;
  /** Effective sample size: cluster count under the headline model. */
  nEff: number;
  /** Cluster-bootstrap CI of median R (headline model), or null when empty. */
  ci: [number, number] | null;
  /** DEVELOPMENT | HOLDOUT. */
  dataScope: string;
}

function fmtBound(value: number): string {
  if (!Number.isFinite(value)) return "n/a";
  return value.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

function gradeWording(grade: string | null): string {
  if (grade == null) return "n/a — HOLDOUT scope is judged by pre-registered criteria";
  if (grade === "D") return "Insufficient evidence";
  return `Meets pre-defined evidence criteria (grade ${grade})`;
}

export function EvidenceGradeBadge({ grade, nEff, ci, dataScope }: EvidenceGradeBadgeProps) {
  return (
    <div className="grade-badge" data-grade={grade ?? "null"}>
      <span className="grade-label">Evidence Grade</span>
      <span className="grade-letter">{grade ?? "n/a"}</span>
      <span className="grade-wording">{gradeWording(grade)}</span>
      <span className="grade-meta">
        n_eff {nEff} · CI [{ci ? `${fmtBound(ci[0])}, ${fmtBound(ci[1])}` : "n/a"}] ·{" "}
        {dataScope}
      </span>
    </div>
  );
}
