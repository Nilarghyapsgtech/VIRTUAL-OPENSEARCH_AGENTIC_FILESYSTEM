from functools import lru_cache

from opensearchpy import OpenSearch

from app.config import Settings, get_settings
from app.fs import OpenSearchFS
from app.opensearch_client import get_opensearch_client
from app.shell import ShellService


@lru_cache(maxsize=1)
def get_fs() -> OpenSearchFS:
    return OpenSearchFS(get_opensearch_client(), get_settings())


@lru_cache(maxsize=1)
def get_shell_service() -> ShellService:
    settings = get_settings()
    return ShellService(get_fs(), max_output_bytes=settings.osfs_max_shell_output_bytes)
