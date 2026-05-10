from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


NodeType = Literal["file", "dir"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RequestContext(BaseModel):
    tenant_id: str = "demo"
    collection_id: str = "docs"
    principals: list[str] = Field(default_factory=lambda: ["group:public"])


class BootstrapResponse(BaseModel):
    ok: bool
    indices: dict[str, str]
    aliases: dict[str, str]


class FileInput(BaseModel):
    path: str
    content: str
    mtime: datetime = Field(default_factory=utc_now)
    acl_principals: list[str] = Field(default_factory=lambda: ["group:public"])
    source_uri: str | None = None
    language: str | None = None


class IngestJsonRequest(RequestContext):
    files: list[FileInput]
    refresh: bool = True
    replace_collection: bool = True


class IngestLocalRequest(RequestContext):
    root_dir: str
    include_globs: list[str] = Field(default_factory=lambda: ["**/*", "*"])
    exclude_globs: list[str] = Field(
        default_factory=lambda: [
            "**/.git/**",
            "**/__pycache__/**",
            "**/.venv/**",
            "**/node_modules/**",
            "**/.DS_Store",
        ]
    )
    default_acl_principals: list[str] = Field(default_factory=lambda: ["group:public"])
    refresh: bool = True
    replace_collection: bool = True


class IngestResponse(BaseModel):
    ok: bool
    tenant_id: str
    collection_id: str
    files_indexed: int
    chunks_indexed: int
    path_nodes_indexed: int
    bulk_errors: list[dict] = Field(default_factory=list)


class Dirent(BaseModel):
    path: str
    basename: str
    type: NodeType
    size_bytes: int | None = None
    mtime: datetime | None = None
    child_count: int | None = None


class StatResponse(Dirent):
    depth: int


class ListResponse(BaseModel):
    path: str
    entries: list[Dirent]


class CatResponse(BaseModel):
    path: str
    content: str
    truncated: bool = False


class FindRequest(RequestContext):
    root: str = "/"
    name_glob: str | None = None
    type: NodeType | None = None
    max_depth: int | None = None
    limit: int = 1000


class FindResponse(BaseModel):
    entries: list[Dirent]
    truncated: bool = False


class GrepFlags(BaseModel):
    recursive: bool = True
    ignore_case: bool = False
    line_numbers: bool = True
    files_with_matches: bool = False
    regex: bool = False
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    limit: int = 1000


class GrepRequest(RequestContext):
    pattern: str
    roots: list[str] = Field(default_factory=lambda: ["/"])
    flags: GrepFlags = Field(default_factory=GrepFlags)


class GrepHit(BaseModel):
    path: str
    line_no: int
    line: str


class GrepResponse(BaseModel):
    hits: list[GrepHit]
    truncated: bool = False


class ShellRunRequest(RequestContext):
    session_id: str = "default"
    command: str
    cwd: str = "/"


class ShellRunResponse(BaseModel):
    session_id: str
    cwd: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    truncated: bool = False
