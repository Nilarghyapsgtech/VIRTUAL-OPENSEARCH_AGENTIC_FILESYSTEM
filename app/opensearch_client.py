from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

from opensearchpy import OpenSearch

from app.config import get_settings


@lru_cache(maxsize=1)
def get_opensearch_client() -> OpenSearch:
    settings = get_settings()
    parsed = urlparse(settings.opensearch_url)
    use_ssl = parsed.scheme == "https"
    port = parsed.port or (443 if use_ssl else 80)

    kwargs = {
        "hosts": [{"host": parsed.hostname or "localhost", "port": port}],
        "use_ssl": use_ssl,
        "verify_certs": settings.opensearch_verify_certs,
        "ssl_assert_hostname": settings.opensearch_verify_certs,
        "ssl_show_warn": settings.opensearch_verify_certs,
        "timeout": settings.opensearch_timeout,
        "max_retries": 2,
        "retry_on_timeout": True,
    }

    if settings.opensearch_username:
        kwargs["http_auth"] = (
            settings.opensearch_username,
            settings.opensearch_password or "",
        )

    return OpenSearch(**kwargs)
