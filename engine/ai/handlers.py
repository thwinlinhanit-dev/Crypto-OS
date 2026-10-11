"""M9 — the data-backed AI tool handlers (the trust boundary, review
Phase 1: "never return success for a computation that did not run").

Only the tools with a real data-backed implementation are registered
here; every other tool returns an explicit NOT_IMPLEMENTED (the executor
in `apps/api/ai.py` enforces that).

Provenance rules (PD-5/PD-6, D-066): a successful result carries a
deterministic ``ref_id`` derived from the canonical JSON of the payload
via sha256 — never Python's ``hash()`` (process-randomized). The
``dataset_version`` is the real one the result read, not a generic "1".
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import Connection, text


def canonical_ref_id(payload: dict[str, Any]) -> str:
    """The deterministic ref_id: sha256 of the canonical JSON (stable across
    processes, unlike Python's ``hash()``)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _audit(conn: Connection, name: str, result: dict[str, Any]) -> None:
    """Audit every executed call (D-062: system.audit_log; the run row and
    agents.tool_call are written by the caller)."""
    conn.execute(
        text(
            "INSERT INTO system.audit_log (actor, action, target, detail) "
            "VALUES ('copilot', 'ai.tool_call', :target, :detail)"
        ),
        {
            "target": name,
            "detail": json.dumps(
                {
                    "tool": name,
                    "ref_id": result.get("ref_id"),
                    "ok": result.get("ok", False),
                },
                sort_keys=True,
            ),
        },
    )


# --------------------------------------------------------------------------
# The three data-backed tools
# --------------------------------------------------------------------------


def get_experiment(conn: Connection, args: dict[str, Any]) -> dict[str, Any]:
    """``get_experiment``: the stored experiment row (spec §9 read tool)."""
    row = conn.execute(
        text(
            "SELECT experiment_id, status, title, dataset_version"
            " FROM research.experiment WHERE experiment_id = :id"
        ),
        {"id": str(args.get("experiment_id", ""))},
    ).first()
    payload: dict[str, Any] = {
        "tool": "get_experiment",
        "experiment_id": str(args.get("experiment_id", "")),
        "dataset_version": row.dataset_version if row else None,
    }
    if row is None:
        payload["ok"] = False
        payload["error"] = "not found"
    else:
        payload["ok"] = True
        payload["status"] = row.status
        payload["title"] = row.title
    payload["ref_id"] = canonical_ref_id(payload)
    _audit(conn, "get_experiment", payload)
    return payload


def list_hypotheses(conn: Connection, args: dict[str, Any]) -> dict[str, Any]:
    """``list_hypotheses``: the stored hypothesis registry (spec §9 read
    tool)."""
    limit = min(int(args.get("limit", 20)), 100)
    rows = conn.execute(
        text(
            "SELECT hyp_id, status, statement FROM research.hypothesis "
            "ORDER BY preregistered_at DESC LIMIT :limit"
        ),
        {"limit": limit},
    ).all()
    payload: dict[str, Any] = {
        "tool": "list_hypotheses",
        "ok": True,
        "limit": limit,
        "hypotheses": [
            {
                "hypothesis_id": str(row.hyp_id),
                "status": row.status,
                "statement": row.statement,
            }
            for row in rows
        ],
    }
    payload["ref_id"] = canonical_ref_id(payload)
    payload["dataset_version"] = None  # registry rows are not dataset-scoped
    _audit(conn, "list_hypotheses", payload)
    return payload


def search_instruments(conn: Connection, args: dict[str, Any]) -> dict[str, Any]:
    """``search_instruments``: the instrument registry (spec §9 read tool)."""
    rows = conn.execute(
        text(
            "SELECT instrument_id, native_symbol, market_type FROM market.instrument"
            " WHERE native_symbol ILIKE :q ORDER BY native_symbol LIMIT 20"
        ),
        {"q": f"%{args.get('query', '')}%"},
    ).all()
    payload: dict[str, Any] = {
        "tool": "search_instruments",
        "ok": True,
        "query": str(args.get("query", "")),
        "instruments": [
            {
                "instrument_id": str(row.instrument_id),
                "symbol": row.native_symbol,
                "market_type": row.market_type,
            }
            for row in rows
        ],
    }
    payload["ref_id"] = canonical_ref_id(payload)
    payload["dataset_version"] = None
    _audit(conn, "search_instruments", payload)
    return payload


#: The tool name -> bound handler map. The executor in apps/api/ai.py
#: refuses any tool NOT in this map (never a fake success).
BOUND_TOOLS: dict[str, Callable[[Connection, dict[str, Any]], dict[str, Any]]] = {
    "get_experiment": get_experiment,
    "list_hypotheses": list_hypotheses,
    "search_instruments": search_instruments,
}
