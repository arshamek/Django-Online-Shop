"""Container health probe: GET /health/ with a Host header Django accepts. Exit 0 = healthy."""

import os
import sys
import urllib.request

host = os.environ.get("ALLOWED_HOSTS", "localhost").split(",")[0].strip() or "localhost"
request = urllib.request.Request("http://127.0.0.1:8000/health/", headers={"Host": host})
try:
    with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310 - fixed loopback URL
        sys.exit(0 if response.status == 200 else 1)
except Exception:  # noqa: BLE001
    sys.exit(1)
