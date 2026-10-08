"""PD-6/PD-13 stored identity values (engine_version, code_version, dataset_schema_version)."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# ASSUMPTION (open question for the owner): the spec requires every result to store
# engine_version but does not define its format. Tracked in docs/DECISIONS.md.
ENGINE_VERSION = "0.1.0"

# Bumped when the bar/event/result JSON shape changes; every result stores it (PD-6).
DATASET_SCHEMA_VERSION = "1"

_SERVICE_VERSION = "0.1.0"


def code_version() -> str:
    """Git HEAD sha; falls back to the marker when the checkout is not a git repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = out.stdout.strip()
    return sha or "unknown"


def service_version() -> str:
    return _SERVICE_VERSION
