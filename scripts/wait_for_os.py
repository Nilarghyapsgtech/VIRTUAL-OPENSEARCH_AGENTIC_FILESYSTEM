from __future__ import annotations

import os
import time
import urllib.request

url = os.getenv("OPENSEARCH_URL", "http://localhost:9200")
for _ in range(60):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status < 500:
                print("OpenSearch is reachable")
                raise SystemExit(0)
    except Exception:
        time.sleep(2)
print("OpenSearch did not become reachable")
raise SystemExit(1)
