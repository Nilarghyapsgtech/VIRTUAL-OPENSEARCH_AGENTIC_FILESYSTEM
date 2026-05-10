from __future__ import annotations

import fnmatch
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk

from app.config import Settings
from app.errors import FsError
from app.mappings import concrete_indices, ensure_indices
from app.models import FileInput, IngestJsonRequest, IngestLocalRequest, IngestResponse
from app.path_utils import (
    ancestor_dir_prefixes_for_file,
    basename,
    content_hash,
    depth,
    extension,
    guess_language,
    is_probably_text_file,
    make_virtual_path_from_local,
    normalize_virtual_path,
    parent_path,
    path_hash,
    path_prefixes,
    unique_sorted,
)


def ingest_json(client: OpenSearch, settings: Settings, request: IngestJsonRequest) -> IngestResponse:
    ensure_indices(client, settings)
    if request.replace_collection:
        delete_collection(client, settings, request.tenant_id, request.collection_id)
    return _index_files(
        client=client,
        settings=settings,
        tenant_id=request.tenant_id,
        collection_id=request.collection_id,
        files=request.files,
        refresh=request.refresh,
    )


def ingest_local(client: OpenSearch, settings: Settings, request: IngestLocalRequest) -> IngestResponse:
    if not settings.osfs_enable_local_ingest:
        raise FsError("EACCES", "local ingest is disabled", status_code=403)

    root = Path(request.root_dir).resolve()
    base = Path(settings.osfs_ingest_base_dir).resolve()
    if root != base and not str(root).startswith(str(base) + os.sep):
        raise FsError(
            "EACCES",
            f"root_dir must be inside configured OSFS_INGEST_BASE_DIR: {base}",
            status_code=403,
        )
    if not root.exists() or not root.is_dir():
        raise FsError("ENOENT", f"no such ingest directory: {root}", status_code=404)

    files: list[FileInput] = []
    for local_path in sorted(root.rglob("*")):
        if not local_path.is_file():
            continue
        rel = local_path.relative_to(root).as_posix()
        if not _included(rel, request.include_globs, request.exclude_globs):
            continue
        if not is_probably_text_file(local_path):
            continue
        try:
            content = local_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = local_path.read_text(encoding="utf-8", errors="replace")

        virtual_path = make_virtual_path_from_local(root, local_path)
        mtime = datetime.fromtimestamp(local_path.stat().st_mtime, tz=timezone.utc)
        files.append(
            FileInput(
                path=virtual_path,
                content=content,
                mtime=mtime,
                acl_principals=request.default_acl_principals,
                source_uri=f"file://{local_path}",
                language=guess_language(virtual_path),
            )
        )

    json_request = IngestJsonRequest(
        tenant_id=request.tenant_id,
        collection_id=request.collection_id,
        principals=request.principals,
        files=files,
        refresh=request.refresh,
        replace_collection=request.replace_collection,
    )
    return ingest_json(client, settings, json_request)


def delete_collection(client: OpenSearch, settings: Settings, tenant_id: str, collection_id: str) -> None:
    indices = concrete_indices(settings)
    query = {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"tenant_id": tenant_id}},
                    {"term": {"collection_id": collection_id}},
                ]
            }
        }
    }
    for index in (indices["files"], indices["chunks"], indices["paths"]):
        client.delete_by_query(index=index, body=query, conflicts="proceed", refresh=True, ignore=[404])


def _index_files(
    client: OpenSearch,
    settings: Settings,
    tenant_id: str,
    collection_id: str,
    files: Iterable[FileInput],
    refresh: bool,
) -> IngestResponse:
    indices = concrete_indices(settings)
    files_by_path: dict[str, FileInput] = {}
    for file in files:
        normalized_path = normalize_virtual_path(file.path)
        if normalized_path == "/":
            raise FsError("EISDIR", "cannot ingest root as a file")
        files_by_path[normalized_path] = file.model_copy(update={"path": normalized_path})

    file_actions: list[dict] = []
    chunk_actions: list[dict] = []

    dir_acls: dict[str, set[str]] = defaultdict(set)
    dir_children: dict[str, set[str]] = defaultdict(set)
    dir_mtimes: dict[str, datetime] = {}

    for normalized_path, file in files_by_path.items():
        acl = file.acl_principals or ["group:public"]
        ph = path_hash(tenant_id, collection_id, normalized_path)
        chash = content_hash(file.content)
        size_bytes = len(file.content.encode("utf-8"))
        ext = extension(normalized_path)
        language = file.language or guess_language(normalized_path)
        file_prefixes = ancestor_dir_prefixes_for_file(normalized_path)
        content_stored = size_bytes <= settings.osfs_max_cat_bytes

        file_doc = {
            "tenant_id": tenant_id,
            "collection_id": collection_id,
            "path_hash": ph,
            "path": normalized_path,
            "path_prefixes": file_prefixes,
            "parent_path": parent_path(normalized_path),
            "basename": basename(normalized_path),
            "extension": ext,
            "type": "file",
            "language": language,
            "size_bytes": size_bytes,
            "mtime": _iso(file.mtime),
            "content_sha256": chash,
            "content": file.content if content_stored else "",
            "content_stored": content_stored,
            "acl_principals": acl,
            "source_uri": file.source_uri,
        }
        file_actions.append(
            {"_op_type": "index", "_index": indices["files"], "_id": ph, "_source": file_doc}
        )

        for chunk in _chunk_content(file.content, settings.osfs_chunk_lines):
            chunk_id = f"{ph}:{chunk['chunk_no']:08d}"
            chunk_doc = {
                "tenant_id": tenant_id,
                "collection_id": collection_id,
                "path_hash": ph,
                "chunk_id": chunk_id,
                "path": normalized_path,
                "path_prefixes": file_prefixes,
                "basename": basename(normalized_path),
                "extension": ext,
                "chunk_no": chunk["chunk_no"],
                "line_start": chunk["line_start"],
                "line_end": chunk["line_end"],
                "content": chunk["content"],
                "content_wildcard": chunk["content"],
                "acl_principals": acl,
                "content_sha256": chash,
            }
            chunk_actions.append(
                {"_op_type": "index", "_index": indices["chunks"], "_id": chunk_id, "_source": chunk_doc}
            )

        parent = parent_path(normalized_path)
        dir_children[parent].add(normalized_path)
        for dir_path in file_prefixes:
            dir_acls[dir_path].update(acl)
            current = dir_mtimes.get(dir_path)
            if current is None or file.mtime > current:
                dir_mtimes[dir_path] = file.mtime

        current = dir_mtimes.get(parent)
        if current is None or file.mtime > current:
            dir_mtimes[parent] = file.mtime

    path_actions = _build_path_actions(
        indices=indices,
        tenant_id=tenant_id,
        collection_id=collection_id,
        files_by_path=files_by_path,
        dir_acls=dir_acls,
        dir_children=dir_children,
        dir_mtimes=dir_mtimes,
    )

    actions = file_actions + chunk_actions + path_actions
    errors: list[dict] = []
    if actions:
        _, errors = bulk(client, actions, raise_on_error=False, request_timeout=settings.opensearch_timeout)
    if refresh:
        for index in (indices["files"], indices["chunks"], indices["paths"]):
            client.indices.refresh(index=index)

    return IngestResponse(
        ok=len(errors) == 0,
        tenant_id=tenant_id,
        collection_id=collection_id,
        files_indexed=len(file_actions),
        chunks_indexed=len(chunk_actions),
        path_nodes_indexed=len(path_actions),
        bulk_errors=errors[:20],
    )


def _build_path_actions(
    indices: dict[str, str],
    tenant_id: str,
    collection_id: str,
    files_by_path: dict[str, FileInput],
    dir_acls: dict[str, set[str]],
    dir_children: dict[str, set[str]],
    dir_mtimes: dict[str, datetime],
) -> list[dict]:
    all_dirs = set(dir_acls.keys()) | {"/"}
    for file_path in files_by_path.keys():
        for prefix in ancestor_dir_prefixes_for_file(file_path):
            all_dirs.add(prefix)
        dir_children[parent_path(file_path)].add(file_path)

    # Add directory child relationships.
    for dir_path in list(all_dirs):
        if dir_path != "/":
            dir_children[parent_path(dir_path)].add(dir_path)

    actions: list[dict] = []

    for dir_path in sorted(all_dirs, key=lambda p: (depth(p), p)):
        acl = unique_sorted(dir_acls.get(dir_path, {"group:public"}))
        ph = path_hash(tenant_id, collection_id, dir_path)
        doc = {
            "tenant_id": tenant_id,
            "collection_id": collection_id,
            "path_hash": ph,
            "path": dir_path,
            "path_prefixes": path_prefixes(dir_path),
            "parent_path": parent_path(dir_path),
            "basename": basename(dir_path),
            "type": "dir",
            "depth": depth(dir_path),
            "child_count": len(dir_children.get(dir_path, set())),
            "size_bytes": 0,
            "mtime": _iso(dir_mtimes.get(dir_path, datetime.now(timezone.utc))),
            "acl_principals": acl,
        }
        actions.append({"_op_type": "index", "_index": indices["paths"], "_id": ph, "_source": doc})

    for file_path, file in files_by_path.items():
        ph = path_hash(tenant_id, collection_id, file_path)
        doc = {
            "tenant_id": tenant_id,
            "collection_id": collection_id,
            "path_hash": ph,
            "path": file_path,
            "path_prefixes": ancestor_dir_prefixes_for_file(file_path),
            "parent_path": parent_path(file_path),
            "basename": basename(file_path),
            "type": "file",
            "depth": depth(file_path),
            "child_count": 0,
            "size_bytes": len(file.content.encode("utf-8")),
            "mtime": _iso(file.mtime),
            "acl_principals": file.acl_principals or ["group:public"],
        }
        actions.append({"_op_type": "index", "_index": indices["paths"], "_id": ph, "_source": doc})

    return actions


def _chunk_content(content: str, chunk_lines: int) -> list[dict]:
    lines = content.splitlines()
    if not lines:
        return []
    chunks: list[dict] = []
    chunk_no = 0
    for start in range(0, len(lines), chunk_lines):
        part = lines[start : start + chunk_lines]
        chunks.append(
            {
                "chunk_no": chunk_no,
                "line_start": start + 1,
                "line_end": start + len(part),
                "content": "\n".join(part),
            }
        )
        chunk_no += 1
    return chunks


def _included(rel_path: str, include_globs: list[str], exclude_globs: list[str]) -> bool:
    included = any(fnmatch.fnmatch(rel_path, pattern) for pattern in include_globs)
    excluded = any(fnmatch.fnmatch(rel_path, pattern) for pattern in exclude_globs)
    return included and not excluded


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
