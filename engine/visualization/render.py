"""M12 — the line/HUD/table renderer (spec §13).

The render graph is declarative JSON: the engine produces it, the web app
renders it (PD-5 - the engine never computes client-side statistics). The
M12 primitives are the §13 line/HUD/table set; the candle overlay and the
heatmap/profile/footprint primitives land with M13/M14 (spec §38.10).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from engine.errors import ValidationError
from engine.visualization.provenance import ProvenanceValue, assert_distinguishable


@dataclass(frozen=True)
class LineSeries:
    """A line series (the §30.1 golden fixture)."""

    key: str
    label: str
    points: tuple[tuple[str, float], ...]  # (ts, value) pairs
    provenance: ProvenanceValue | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "points": [[ts, value] for ts, value in self.points],
        }
        if self.provenance is not None:
            payload["provenance"] = self.provenance.as_dict()
        return payload


@dataclass(frozen=True)
class HudElement:
    """A HUD element (the §20 HUD framework; §30.3)."""

    key: str
    value: Any
    provenance: ProvenanceValue | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"key": self.key, "value": self.value}
        if self.provenance is not None:
            payload["provenance"] = self.provenance.as_dict()
        return payload


@dataclass(frozen=True)
class TableRow:
    """A table row (the §13 table primitive)."""

    cells: tuple[tuple[str, Any], ...]
    provenance: ProvenanceValue | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"cells": [[key, value] for key, value in self.cells]}
        if self.provenance is not None:
            payload["provenance"] = self.provenance.as_dict()
        return payload


def render_line(
    series: Sequence[LineSeries],
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    """The deterministic line render graph (the §30.1 golden fixture).

    Identical input → identical output. ``as_of`` truncates every series at
    the replay boundary (§23) before the graph is built.
    """
    if not series:
        raise ValidationError("a line render needs at least one series", {})
    rendered = []
    for item in series:
        points = item.points
        if as_of is not None:
            points = tuple((ts, value) for ts, value in points if ts <= as_of)
        rendered.append(
            {
                "key": item.key,
                "label": item.label,
                "points": [[ts, value] for ts, value in points],
                **(
                    {"provenance": item.provenance.as_dict()}
                    if item.provenance is not None
                    else {}
                ),
            }
        )
    return {"kind": "LINE", "as_of": as_of, "series": rendered}


def render_hud(
    elements: Sequence[HudElement],
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    """The deterministic HUD render graph (the §30.3 golden fixture)."""
    if not elements:
        raise ValidationError("a HUD render needs at least one element", {})
    provenances = [e.provenance for e in elements if e.provenance is not None]
    assert_distinguishable(provenances)
    return {
        "kind": "HUD",
        "as_of": as_of,
        "elements": [e.as_dict() for e in elements],
    }


def render_table(
    rows: Sequence[TableRow],
    *,
    columns: Sequence[str],
    as_of: str | None = None,
) -> dict[str, Any]:
    """The deterministic table render graph (the §13 table primitive)."""
    if not rows:
        raise ValidationError("a table render needs at least one row", {})
    if not columns:
        raise ValidationError("a table render needs its columns", {})
    return {
        "kind": "TABLE",
        "as_of": as_of,
        "columns": list(columns),
        "rows": [r.as_dict() for r in rows],
    }
