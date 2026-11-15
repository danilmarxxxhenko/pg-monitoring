"""Tiny CLI used by `make chaos` and healthchecks (the image has no curl).

Usage:
    python -m app.ctl health
    python -m app.ctl chaos <mode>
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"


def _call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.load(resp)


def main(argv: list[str]) -> int:
    if argv[:1] == ["health"]:
        _call("GET", "/health")
        return 0
    if argv[:1] == ["chaos"] and len(argv) == 2:
        try:
            print(json.dumps(_call("POST", "/chaos", {"mode": argv[1]})))
        except urllib.error.HTTPError as exc:
            print(exc.read().decode(), file=sys.stderr)
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
