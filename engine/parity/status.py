"""Section 14.2 layer-1 parity status vocabulary (spec line 592).

The five statuses are fixed by the spec: ``EXACT`` |
``EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE`` | ``DATA_MISMATCH`` |
``ENGINE_BUG`` (blocks) | ``UNKNOWN`` (blocks), and "Pass = zero ENGINE_BUG and
zero UNKNOWN". A status classifies a *comparison*; it never assigns blame -
failure never automatically means the engine is wrong (line 592), and the tool
reports numbers plus category while a human decides.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class ParityStatus(StrEnum):
    """Spec line 592 parity status categories."""

    EXACT = "EXACT"
    EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE = "EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE"
    DATA_MISMATCH = "DATA_MISMATCH"
    ENGINE_BUG = "ENGINE_BUG"
    UNKNOWN = "UNKNOWN"


#: Worst-wins order for the overall status: a run that cannot be compared at
#: all (UNKNOWN) outranks an engine that failed (ENGINE_BUG), which outranks
#: data that does not line up (DATA_MISMATCH), which outranks the two passing
#: plot outcomes.
_SEVERITY: tuple[ParityStatus, ...] = (
    ParityStatus.UNKNOWN,
    ParityStatus.ENGINE_BUG,
    ParityStatus.DATA_MISMATCH,
    ParityStatus.EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE,
    ParityStatus.EXACT,
)


def is_pass(status: ParityStatus) -> bool:
    """Spec line 592: pass = zero ENGINE_BUG and zero UNKNOWN."""
    return status not in (ParityStatus.ENGINE_BUG, ParityStatus.UNKNOWN)


def worst_of(*statuses: ParityStatus) -> ParityStatus:
    """Worst of the given statuses; nothing given is UNKNOWN (4.2 fail-closed)."""
    if not statuses:
        return ParityStatus.UNKNOWN
    return min(statuses, key=_SEVERITY.index)


def overall(per_plot: Mapping[str, ParityStatus]) -> ParityStatus:
    """Worst status across the compared plots; nothing compared is UNKNOWN.

    "cannot be compared" must never read as "passed" (design note 4.2).
    """
    return worst_of(*per_plot.values())
