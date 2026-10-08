# FAST_RECLAIM

> **Status: `SPEC_REQUIRED`**

This is an intentional placeholder. The current implementation specification
states that `FAST_RECLAIM` has no supplied deterministic rules. Under **PD-4
(SPEC BEFORE CODE)**, agents MUST NOT invent, infer, approximate, or implement
this event from its name.

## 1. Identity
- `event_id`: `FAST_RECLAIM`
- `detector_id`: `TBD`
- `version`: `TBD`
- `status`: `SPEC_REQUIRED`
- `direction`: `TBD`
- `source`: Owner-supplied specification required

## 2. Purpose
**TBD — owner definition required.**

Do not infer meaning from the event name.

## 3. Required Inputs
**TBD — owner definition required.**

## 4. Parameters
**TBD — owner definition required.**

No parameters or defaults may be invented.

## 5. Derived Features
**TBD — owner definition required.**

## 6. Trigger Rule
**TBD — owner definition required.**

```text
raw_event = NOT_DEFINED
```

This is deliberately non-executable.

## 7. Cooldown / De-duplication
**TBD — owner definition required.**

Do not assume RVOL_CLIMAX cooldown semantics apply.

## 8. Invalidation
**TBD — owner definition required.**

Do not reuse another event's invalidation rule without explicit approval.

## 9. Outcomes
After the event rule is approved, define the standard horizons:
`[1,2,3,5,10,15,20,30,40,50]`.

## 10. Causality / Lookahead
**TBD — owner definition required.**

The final specification must define the causal `event_time` and satisfy PD-2.

## 11. Edge Cases
**TBD — owner definition required.**

## 12. Golden Fixtures
**NOT AVAILABLE — specification required first.**

Do not manufacture expected outputs.

## 13. Parity / Reference Validation
**TBD — owner definition required.**

## 14. Implementation Gate
This event remains `SPEC_REQUIRED` until the owner supplies:
- [ ] purpose and exclusions
- [ ] direction
- [ ] required inputs
- [ ] exact parameters/defaults
- [ ] exact derived features
- [ ] exact trigger
- [ ] cooldown/de-duplication
- [ ] invalidation
- [ ] causal event timestamp
- [ ] edge cases
- [ ] golden fixtures
- [ ] truncation-invariance acceptance criteria
- [ ] parity/reference criteria where applicable

**PD-4 BLOCK: Do not implement `FAST_RECLAIM` before this gate is complete.**

## 15. Explicit Agent Instruction

> **STOP AND ASK THE OWNER FOR THE `FAST_RECLAIM` SPECIFICATION.**
>
> Do not create code, tests, fixtures, thresholds, heuristics, approximations,
> strategy entries, scanner rules, or UI semantics based on an assumed definition.
