"""Strategy/experiment identity: canonical JSON hashes (PD-6, D-037 form).

Same canonical form everywhere — sorted keys, tight separators, UTF-8 — so a
stored backtest can name the exact strategy definition it ran
(`strategy_hash`) and freeze can hash its trades byte-identically to what a
later verify recomputes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Byte-stable JSON: sorted keys, tight separators, no float drift."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
