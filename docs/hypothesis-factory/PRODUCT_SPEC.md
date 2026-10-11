# Hypothesis Factory — Product Spec

## 1. What it is

A research module inside Crypto OS that turns data, phenomena and ideas into
**formally defined, falsifiable, versioned hypotheses** tested by
**reproducible experiments** with **honest statistical evidence**.

It is not a chatbot, a signal generator, or an autonomous trader.

## 2. The evidence ladder (spec §1 — never interchangeable)

1. AI-generated idea (a proposal, never a finding)
2. Formally specified hypothesis (validated definition)
3. Hypothesis with preliminary statistical evidence (exploratory)
4. Hypothesis surviving multiple-testing + robustness (research-validated)
5. Hypothesis passing out-of-sample validation (confirmatory)
6. Strategy passing paper-trading evaluation
7. Strategy approved for potential production consideration

The Hypothesis Factory owns levels 1–5; level 6–7 stay out of scope (no
live-trading authorization exists anywhere in Crypto OS — PD-1).

## 3. Primary workflow (spec §3.1)

Select a dataset/universe → select a research domain → generate candidates →
inspect/edit → check data availability → deduplicate → rank by priority →
approve for testing → generate an experiment spec → execute permitted
experiments → review statistics → run robustness/falsification → reserve
untouched data for final validation → accept/reject/inconclusive → save the
research history → generate reports → interact via API/MCP.

## 4. Generation sources (spec §4)

- **A — AI-assisted idea generation:** an interchangeable reasoning-model
  interface (the M9 `LLMProvider`); a proposal is never a finding; no
  fabricated statistics.
- **B — Pattern discovery (deterministic):** univariate screening,
  conditional forward returns, quantiles, interactions, event windows,
  regime conditioning, cross-asset lead-lag, volatility state, funding/OI
  interaction, relative strength, redundancy. Advanced methods (mutual
  information, change-point, clustering, symbolic rules, tree interactions,
  causal inference) are optional extensions.
- **C — Research-gap discovery:** the underexplored features/horizons/regimes,
  the hypotheses lacking OOS, the contradictory findings, the effects never
  re-tested after costs.
- **D — Human ideas:** a natural-language research question is preserved
  verbatim and formalized.

## 5. Taxonomy (spec §5 — extensible)

Price & trend · derivatives · capitulation & recovery · cross-asset ·
on-chain · fundamental/tokenomics · narrative & information · market regimes
· **negative hypotheses** (first-class: "indicator X adds nothing after
momentum" is a research object, not a failure).

## 6. The dashboard (spec §20)

Overview · Discover · Backlog · Detail · Experiment Lab · Validation Center ·
Evidence Graph · Reports · Provider/Agent settings. Every metric comes from
real data (PD-5); an empty state is an empty state — never a fabricated
number.

## 7. What the owner answers (spec §1.1)

What relationships exist? Which are unexplored? Which deserve research time?
What supports/contradicts each? Could it be overfitting/selection/look-ahead/
survivorship/extreme-winners? Does it persist across assets, periods, regimes,
parameters? Is it economically useful after costs? What to investigate next?

## 8. Non-goals

- No live-trading authorization (PD-1, spec §21).
- No fabricated data, results or AI availability (spec §27.5).
- No hidden failures: "no effect", "insufficient data", "invalid experiment",
  "execution failure", "statistical test failure", "provider unavailable" are
  six distinct outcomes (spec §22).
- No uncontrolled generate-and-test loop (spec §17.2).
