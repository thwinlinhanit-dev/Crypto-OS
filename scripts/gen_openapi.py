"""Write ``packages/client/src/openapi.json`` from the running app object.

No server required: the OpenAPI document is built from ``create_app()`` so the
generated client always matches the code that ships (PD-6, reproducibility).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.main import create_app  # noqa: E402  (path bootstrap above)

OUT = ROOT / "packages" / "client" / "src" / "openapi.json"


def main() -> int:
    document: dict[str, object] = create_app().openapi()
    OUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
