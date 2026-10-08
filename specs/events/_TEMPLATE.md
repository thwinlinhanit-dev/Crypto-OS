# CryptoOS Event Specification Template

> **Status:** TEMPLATE  
> Every event must have a deterministic specification before implementation.

## 1. Identity
- `event_id`: `<EVENT_ID>`
- `detector_id`: `<DETECTOR_ID>`
- `version`: `<VERSION>`
- `status`: `SPEC_REQUIRED | DRAFT | READY | DEPRECATED`
- `direction`: `LONG | SHORT | BOTH`
- `source`: `<authoritative source>`

## 2. Purpose
### Intended meaning
`<EXPLICIT DEFINITION>`

### Non-goals / exclusions
- `<EXPLICIT EXCLUSION>`

## 3. Required Inputs
| Input | Exact definition | Warmup | Causal |
|---|---|---:|---|
| `<input>` | `<definition>` | `<N>` | YES/NO |

## 4. Parameters
| Parameter | Type | Required | Default | Constraints | Meaning |
|---|---|---|---|---|---|
| `<param>` | `<type>` | YES/NO | `<value/NONE>` | `<constraint>` | `<meaning>` |

No hidden defaults. Missing undocumented research parameters must fail closed.

## 5. Derived Features
```text
<FEATURE> = <EXACT DEFINITION>
```
Define seeds, nulls, lookbacks, and boundary behavior.

## 6. Trigger Rule
```text
raw_event = <EXACT BOOLEAN EXPRESSION>
```
All conditions must be deterministic and causal.

## 7. Cooldown / De-duplication
```text
<EXACT FORMULA OR NONE>
```
State whether cooldown counts raw occurrences or emitted events.

## 8. Invalidation
```text
invalidation_price = <EXACT RULE>
R_basis = <EXACT BASIS>
```
Event-study invalidation is separate from strategy stops unless explicitly stated.

## 9. Event Object
Must populate the standard event envelope:
`event_id`, `detector_id`, `event_type`, `version`, `params_hash`,
`instrument_id`, `timeframe`, `event_time`, `direction`, `ref_price`,
`invalidation_price`, `regime`, `features`, `trigger.rules`, `lookback_gap`,
`dataset_version`, `engine_version`, `data_quality`.

Displayed fields use `OBSERVED | CALCULATED | INFERRED | AI` provenance.

## 10. Outcomes
Default horizons:
`[1,2,3,5,10,15,20,30,40,50]`

Define `fwd_ret_pct`, `fwd_ret_R`, `mfe_R`, `mae_R`, `bars_to_mfe`,
`bars_to_mae`, `hit_invalidation`, and `complete`.

Incomplete horizons are excluded from statistics.

## 11. Causality / Lookahead
State exactly what is known at `event_time`. Only closed bars at or before the
event timestamp may affect detection.

## 12. Edge Cases
Define warmup, missing/excluded ranges, null/zero denominators, duplicate
timestamps, equality boundaries, flat candles, incomplete bars, and
same-bar ambiguity.

## 13. Golden Fixtures
Provide positive, negative, boundary, warmup, missing-data, cooldown (if used),
and truncation-invariance fixtures. Store expected outputs as versioned JSON.

## 14. Parity / Reference Validation
Record authoritative source/version, fixture, tolerance, and known platform
differences. Never silently accept parity mismatches.

## 15. Versioning
Any rule, parameter, default, feature, cooldown, invalidation, or outcome
change requires a new event-spec version. Old versions remain runnable.

## 16. Implementation Gate
- [ ] Purpose and non-goals explicit
- [ ] Direction explicit
- [ ] Inputs explicit
- [ ] Parameters/defaults explicit
- [ ] Derived features deterministic
- [ ] Trigger exact
- [ ] Cooldown exact or `NONE`
- [ ] Invalidation exact
- [ ] Outcomes defined
- [ ] Lookahead/causality defined
- [ ] Edge cases defined
- [ ] Golden fixtures exist
- [ ] Truncation-invariance test exists
- [ ] Parity criteria exist where applicable
- [ ] Owner approval recorded

**Until the gate is complete, agents MUST NOT implement the event.**
