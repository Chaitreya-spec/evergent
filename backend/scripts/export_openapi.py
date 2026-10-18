"""Write the API's OpenAPI document, which the frontend generates its types from.

    .venv/bin/python scripts/export_openapi.py            # writes openapi.json
    .venv/bin/python scripts/export_openapi.py --check    # fails if it is stale
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app

TARGET = Path(__file__).resolve().parents[1] / "openapi.json"


def main() -> int:
    document = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text() != document:
            print("openapi.json is out of date: run scripts/export_openapi.py", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(document)
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
