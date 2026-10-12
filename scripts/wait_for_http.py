"""Wait until an HTTP endpoint responds (or exit non-zero on timeout)."""

from __future__ import annotations

import sys
import time
import urllib.request


def wait_for_http(url: str, timeout: float = 60.0) -> int:
    """Poll ``url`` until it returns any HTTP response or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)  # noqa: S310 (local dev)
            return 0
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    print(f"timed out waiting for {url}")
    return 1


if __name__ == "__main__":
    target = sys.argv[1]
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    sys.exit(wait_for_http(target, limit))
