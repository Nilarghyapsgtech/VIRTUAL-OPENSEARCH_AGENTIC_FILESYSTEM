from __future__ import annotations

import json
import urllib.request

BASE_URL = "http://localhost:8000"


def post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


if __name__ == "__main__":
    print(json.dumps(post("/admin/bootstrap", {}), indent=2))
    print(
        json.dumps(
            post(
                "/admin/ingest/local",
                {
                    "tenant_id": "demo",
                    "collection_id": "docs",
                    "root_dir": "/data/ingest/sample_docs",
                    "principals": ["group:public"],
                    "default_acl_principals": ["group:public"],
                    "replace_collection": True,
                },
            ),
            indent=2,
        )
    )
    print(
        json.dumps(
            post(
                "/shell/run",
                {
                    "tenant_id": "demo",
                    "collection_id": "docs",
                    "session_id": "demo-1",
                    "principals": ["group:public"],
                    "command": "grep -rin \"OAuth\" /docs | head -20",
                },
            ),
            indent=2,
        )
    )
