"""M12 — the deterministic dataset API (spec §9).

Programs never access PostgreSQL/Parquet directly. Every dataset response
carries the §9 envelope (dataset_version, schema_version, source, venue,
instrument_id, timeframe, as_of, quality); unavailable/incomplete data
returns an explicit status, never a substitution (§3).

The replay invariant (§23) is enforced here: ``truncate_as_of`` removes
every row after ``as_of``, so ``program(full, as_of=t) ==
program(truncate_at_t)``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from engine.errors import ValidationError
from engine.visualization.models import assert_valid_quality_state

#: The §9 envelope keys every dataset response carries.
ENVELOPE_KEYS: tuple[str, ...] = (
    "dataset_version",
    "schema_version",
    "source",
    "venue",
    "instrument_id",
    "timeframe",
    "as_of",
    "quality",
)


@dataclass(frozen=True)
class DatasetResponse:
    """The §9 dataset envelope + rows."""

    dataset: str
    dataset_version: str
    schema_version: str
    source: str
    venue: str
    instrument_id: str
    timeframe: str
    as_of: str | None
    quality: Mapping[str, Any]
    rows: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "dataset_version": self.dataset_version,
            "schema_version": self.schema_version,
            "source": self.source,
            "venue": self.venue,
            "instrument_id": self.instrument_id,
            "timeframe": self.timeframe,
            "as_of": self.as_of,
            "quality": dict(self.quality),
            "rows": [dict(row) for row in self.rows],
        }


def assert_datasets_declared(
    declared: Sequence[str],
    requested: Sequence[str],
) -> None:
    """Permission safety (§30): an undeclared dataset is refused, never
    fetched (the M9 closed-registry pattern applied to datasets, D-079)."""
    declared_set = set(declared)
    for dataset in requested:
        if dataset not in declared_set:
            raise ValidationError(
                "undeclared dataset requested (§3 permission safety)",
                {
                    "dataset": dataset,
                    "declared": sorted(declared_set),
                },
            )


def truncate_as_of(
    rows: Sequence[Mapping[str, Any]],
    as_of: str | None,
    *,
    time_key: str = "ts",
) -> tuple[Mapping[str, Any], ...]:
    """The §23 replay invariant: rows with ``ts > as_of`` are removed.

    ``as_of=None`` means "live" (no truncation). The invariant
    ``program(full_history, as_of=t) == program(history_truncated_at_t)``
    is asserted in the tests.
    """
    if as_of is None:
        return tuple(rows)
    kept = []
    for row in rows:
        value = row.get(time_key)
        if value is None:
            raise ValidationError(
                "row has no time key; as_of truncation is impossible",
                {"time_key": time_key, "row": dict(row)},
            )
        if str(value) <= as_of:
            kept.append(row)
    return tuple(kept)


def quality_of(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected: int | None = None,
) -> dict[str, Any]:
    """The §9 quality block (§3: missing data fails explicitly, never
    substitutes).

    - no rows at all → ``DATA_UNAVAILABLE``;
    - fewer than ``expected`` → ``DATA_INCOMPLETE`` (the shortfall is
      reported, never filled);
    - otherwise → ``GREEN``.
    """
    if not rows:
        return {"state": "DATA_UNAVAILABLE", "completeness": 0.0, "n_rows": 0}
    if expected is not None and len(rows) < expected:
        return {
            "state": "DATA_INCOMPLETE",
            "completeness": len(rows) / expected,
            "n_rows": len(rows),
            "expected": expected,
        }
    return {"state": "GREEN", "completeness": 1.0, "n_rows": len(rows)}


def assert_quality_ok(quality: Mapping[str, Any]) -> None:
    """A dataset response with an explicit failure state is refused rather
    than rendered (§3: no silent substitution)."""
    state = quality.get("state")
    assert_valid_quality_state(str(state))
    if state in ("DATA_UNAVAILABLE", "DATA_INCOMPLETE"):
        raise ValidationError(
            "dataset is not usable",
            {"state": state, "quality": dict(quality)},
        )
