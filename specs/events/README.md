# PD-4 Event Specification Manifest

PD-4 = **SPEC BEFORE CODE**.

Required files:
- `_TEMPLATE.md`
- `FAST_RECLAIM.md`
- `TOP_CAPIT.md`
- `TOP_CLIMAX.md`
- `TOPFAIL.md`
- `CAPITFAIL.md`
- `CAPIT_RECLAIM.md`

All six named events are intentionally `SPEC_REQUIRED`.

The authoritative v1 detector currently defines RVOL_CLIMAX event types
`CONT`, `CAPIT`, `FAIL`, and `TOP`. The six files above are future placeholders
and must not be conflated with those event types.

In particular, `TOP` is not automatically equivalent to `TOP_CAPIT`,
`TOP_CLIMAX`, or `TOPFAIL`.
