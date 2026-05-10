from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    opensearch_url: str = Field(default="http://localhost:9200", alias="OPENSEARCH_URL")
    opensearch_username: str | None = Field(default=None, alias="OPENSEARCH_USERNAME")
    opensearch_password: str | None = Field(default=None, alias="OPENSEARCH_PASSWORD")
    opensearch_verify_certs: bool = Field(default=False, alias="OPENSEARCH_VERIFY_CERTS")
    opensearch_timeout: int = Field(default=20, alias="OPENSEARCH_TIMEOUT")

    osfs_index_prefix: str = Field(default="osfs", alias="OSFS_INDEX_PREFIX")
    osfs_default_tenant: str = Field(default="demo", alias="OSFS_DEFAULT_TENANT")
    osfs_default_collection: str = Field(default="docs", alias="OSFS_DEFAULT_COLLECTION")
    osfs_default_principals: str = Field(default="group:public", alias="OSFS_DEFAULT_PRINCIPALS")

    osfs_chunk_lines: int = Field(default=200, alias="OSFS_CHUNK_LINES")
    osfs_max_cat_bytes: int = Field(default=1_000_000, alias="OSFS_MAX_CAT_BYTES")
    osfs_max_shell_output_bytes: int = Field(default=500_000, alias="OSFS_MAX_SHELL_OUTPUT_BYTES")
    osfs_max_search_hits: int = Field(default=5000, alias="OSFS_MAX_SEARCH_HITS")

    osfs_enable_local_ingest: bool = Field(default=True, alias="OSFS_ENABLE_LOCAL_INGEST")
    osfs_ingest_base_dir: str = Field(default="/data/ingest", alias="OSFS_INGEST_BASE_DIR")

    @field_validator("opensearch_username", "opensearch_password", mode="before")
    @classmethod
    def empty_string_to_none(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value

    @property
    def default_principals(self) -> List[str]:
        return [p.strip() for p in self.osfs_default_principals.split(",") if p.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
