"""M13 — the layout persistence contract (spec §12; OQ-10's medium).

The persisted canvas state: the layer stack + viewport + visible set, as a
versioned, hashable object (the M12 ``program_hash`` pattern, D-083). The
engine ships the serializable contract; the browser store remains
``localStorage`` (D-012) - the §12 "layout persisted" question, OQ-10.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from engine.visualization.canvas import LayerStack
from engine.visualization.viewport import Viewport


def layout_hash(payload: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON (D-083): a layout change is a new hash,
    stable across processes (never Python's ``hash()``)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Layout:
    """The persisted canvas state (spec §12): layers + viewport + version."""

    name: str
    stack: LayerStack
    viewport: Viewport
    version: str = "1.0.0"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "stack": self.stack.as_dict(),
            "viewport": self.viewport.as_dict(),
        }

    def hash(self) -> str:
        """The layout hash: the same canvas state hashes identically."""
        return layout_hash(self.as_dict())
