"""Section 14.2 line 593: the TradingView run metadata captured with each run.

Nine required fields, no defaults (PD-11): a missing sidecar, an unreadable
sidecar or a missing field fails the run closed as ``UNKNOWN`` - there is no
"reasonable default" for a TradingView run setting (design note 4.4). The
sidecar sits next to the export at ``fixtures/parity/<name>.meta.json`` (or is
passed with ``--meta``).

``bar_magnifier`` is recorded verbatim and warned about when it is not the
expected ``OFF``: the spec lists it as a platform difference to *check*, not as
an automatic failure.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.errors import ValidationError

#: The nine fields spec line 593 records with every parity run.
REQUIRED_FIELDS: tuple[str, ...] = (
    "script_version",
    "symbol",
    "timeframe",
    "date_range",
    "bar_magnifier",
    "commission",
    "slippage",
    "fill_assumptions",
    "export_date",
)

#: Spec line 593: the Bar Magnifier setting a comparable export expects.
EXPECTED_BAR_MAGNIFIER = "OFF"


@dataclass(frozen=True, slots=True)
class TradingViewRunMetadata:
    """Verbatim TradingView run settings (spec line 593)."""

    script_version: str
    symbol: str
    timeframe: str
    date_range: str
    bar_magnifier: str
    commission: str
    slippage: str
    fill_assumptions: str
    export_date: str

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> TradingViewRunMetadata:
        """Build from a sidecar object; anything missing or blank is refused."""
        values: dict[str, str] = {}
        missing: list[str] = []
        for field in REQUIRED_FIELDS:
            raw = document.get(field)
            if isinstance(raw, str) and raw.strip():
                values[field] = raw.strip()
            else:
                missing.append(field)
        if missing:
            raise ValidationError(
                "tradingview run metadata is incomplete",
                {"missing": missing, "required": list(REQUIRED_FIELDS)},
            )
        return cls(
            script_version=values["script_version"],
            symbol=values["symbol"],
            timeframe=values["timeframe"],
            date_range=values["date_range"],
            bar_magnifier=values["bar_magnifier"],
            commission=values["commission"],
            slippage=values["slippage"],
            fill_assumptions=values["fill_assumptions"],
            export_date=values["export_date"],
        )

    def to_document(self) -> dict[str, str]:
        """The sidecar-shaped document embedded in the report verbatim."""
        return {
            "script_version": self.script_version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "date_range": self.date_range,
            "bar_magnifier": self.bar_magnifier,
            "commission": self.commission,
            "slippage": self.slippage,
            "fill_assumptions": self.fill_assumptions,
            "export_date": self.export_date,
        }


def load_metadata(path: Path) -> TradingViewRunMetadata:
    """Read one sidecar file; a missing or malformed file raises (fail closed)."""
    if not path.is_file():
        raise ValidationError(
            "tradingview run metadata sidecar is missing",
            {"path": path.as_posix(), "expected_fields": list(REQUIRED_FIELDS)},
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(
            "tradingview run metadata sidecar is unreadable",
            {"path": path.as_posix()},
        ) from exc
    if not isinstance(document, dict):
        raise ValidationError(
            "tradingview run metadata sidecar must be a JSON object",
            {"path": path.as_posix()},
        )
    return TradingViewRunMetadata.from_document(document)
