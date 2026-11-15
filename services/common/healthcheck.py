"""Container healthcheck: `python -m common.healthcheck http://127.0.0.1:8000/health`.

Slim Python images ship without curl/wget, so the check is done in Python.
"""
from __future__ import annotations

import sys
import urllib.request


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/health"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            return 0 if resp.status == 200 else 1
    except Exception as exc:  # noqa: BLE001
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
