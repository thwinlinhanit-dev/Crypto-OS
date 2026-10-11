# Hypothesis Factory — Research Methodology

## 1. The research cycle (spec §1)

DATA → OBSERVATIONS → CANDIDATE HYPOTHESES → FORMAL DEFINITIONS →
EXPERIMENTS → STATISTICAL TESTS → ROBUSTNESS CHECKS → OUT-OF-SAMPLE
VALIDATION → EVIDENCE-BASED CONCLUSIONS.

## 2. Causal discipline (PD-2)

Every feature/event/signal uses only bars closed at or before its
timestamp (the M4 truncation-invariance suite is the precedent). A feature
unavailable at the decision timestamp cannot be used in a tradable signal
without explicitly modeling its later availability (spec §6).

## 3. The statistical contract (spec §11)

- Methods chosen by the question, distribution, sample size and dependence
  structure — never a default independent-sample test on dependent series.
- Overlapping/serially-dependent observations → block bootstrap, HAC-style
  inference, or non-overlapping samples.
- Required results: n, effective n, mean/median, dispersion, effect size,
  CIs, the null/alternative, the test statistic, raw p, adjusted p, the
  correction method, missing obs, the baseline, the difference, outlier
  sensitivity, known limitations.
- A p-value is never reported without the claim and the method.
- Forward-return horizons: 1/3/5/10/20/40 bars (configurable); the return
  formula (simple vs log vs risk-normalized) is declared per experiment.
- **Statistical significance ≠ economic significance** (spec §11.3): the
  economic threshold is predeclared, never retrofitted.

## 4. Multiple-testing control (spec §12 — mandatory)

- A research ledger records ALL attempted hypotheses/experiments, including
  failures and abandonments.
- The testing family + correction procedure is declared BEFORE the
  inferential analysis; the correction method is never chosen after seeing
  which makes results look best.
- Methods: Benjamini-Hochberg FDR; family-wise error where justified;
  White's Reality Check / PBO / Deflated Sharpe for strategy selection;
  sensitivity across the number of attempted strategies.
- Exploratory vs confirmatory is explicit; the final holdout is never a
  second development set.

## 5. Robustness & falsification (spec §13)

Parameter (the surface, not the best point) · asset (concentration is a
finding) · regime (contemporaneous labels only — PD-2) · time (structural
breaks, year dependence) · outlier dependence (top-1/5/10% contribution,
trimmed/winsorized as clearly-labeled diagnostics; the original result stays
visible) · negative controls & placebo (randomized/shifted timing, alternative
definitions, non-event periods — never naive shuffling of time series).

The falsification agent may recommend rejection and is not rewarded for
finding support (spec §16.6).

## 6. Validation statuses (spec §14.2)

NOT_TESTED · EXPLORATORY · NEEDS_MORE_DATA · INVALID_EXPERIMENT ·
FAILED_STATISTICAL_TEST · FAILED_ROBUSTNESS · FAILED_OUT_OF_SAMPLE ·
PASSED_RESEARCH_VALIDATION · PAPER_TRADING · PAPER_TRADING_PASSED ·
REQUIRES_HUMAN_REVIEW · ARCHIVED.

Statuses derive from explicit rules + actual results; an AI model cannot
promote a hypothesis by expressing confidence (spec §14.2).

## 7. Synthetic methodology tests (spec §23.3)

The suite ships synthetic datasets with known properties and asserts the
pipeline catches them: a null feature; a true positive; a confounded
positive; an outlier-only effect; a look-ahead feature; in-sample-only
performance; and multiple nulls where uncorrected testing creates false
discoveries.
