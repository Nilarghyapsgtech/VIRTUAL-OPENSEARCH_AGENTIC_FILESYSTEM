from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Iterable

from opensearchpy import NotFoundError, OpenSearch

from app.config import Settings
from app.errors import FsError
from app.mappings import aliases
from app.models import Dirent, FindRequest, GrepFlags, GrepHit, RequestContext, StatResponse
from app.path_utils import basename, depth, normalize_virtual_path, path_hash


@dataclass(frozen=True)
class ReadResult:
    content: str
    truncated: bool = False


@dataclass(frozen=True)
class FindResult:
    entries: list[Dirent]
    truncated: bool = False


@dataclass(frozen=True)
class GrepResult:
    hits: list[GrepHit]
    truncated: bool = False


class OpenSearchFS:
    """Read-only virtual filesystem implemented on OpenSearch indices."""

    def __init__(self, client: OpenSearch, settings: Settings):
        self.client = client
        self.settings = settings
        self.aliases = aliases(settings)

    def stat(self, ctx: RequestContext, path: str, cwd: str = "/") -> StatResponse:
        normalized = normalize_virtual_path(path, cwd)
        ph = path_hash(ctx.tenant_id, ctx.collection_id, normalized)
        try:
            hit = self.client.get(index=self.aliases["paths"], id=ph)
        except NotFoundError:
            raise FsError("ENOENT", f"no such file or directory: {normalized}", status_code=404)

        source = hit.get("_source", {})
        self._assert_acl(source, ctx.principals, normalized)
        return self._stat_from_source(source)

    def readdir(self, ctx: RequestContext, path: str, cwd: str = "/") -> list[Dirent]:
        normalized = normalize_virtual_path(path, cwd)
        stat = self.stat(ctx, normalized)
        if stat.type != "dir":
            raise FsError("ENOTDIR", f"not a directory: {normalized}")

        body = {
            "size": min(self.settings.osfs_max_search_hits, 10000),
            "_source": ["path", "basename", "type", "size_bytes", "mtime", "child_count"],
            "query": {
                "bool": {
                    "filter": self._base_filters(ctx)
                    + [{"term": {"parent_path": normalized}}]
                }
            },
            "sort": [{"type": {"order": "asc"}}, {"basename": {"order": "asc"}}],
        }
        response = self.client.search(index=self.aliases["paths"], body=body)
        entries = [self._dirent_from_source(hit["_source"]) for hit in response["hits"]["hits"]]
        # The root node has parent_path=/, so remove it when listing /.
        return [entry for entry in entries if entry.path != normalized]

    def read_file(
        self,
        ctx: RequestContext,
        path: str,
        cwd: str = "/",
        max_bytes: int | None = None,
    ) -> ReadResult:
        normalized = normalize_virtual_path(path, cwd)
        ph = path_hash(ctx.tenant_id, ctx.collection_id, normalized)
        max_bytes = max_bytes if max_bytes is not None else self.settings.osfs_max_cat_bytes

        try:
            hit = self.client.get(index=self.aliases["files"], id=ph)
        except NotFoundError:
            stat = self.stat(ctx, normalized)
            if stat.type == "dir":
                raise FsError("EISDIR", f"illegal operation on a directory: {normalized}")
            raise FsError("ENOENT", f"no such file: {normalized}", status_code=404)

        source = hit.get("_source", {})
        self._assert_acl(source, ctx.principals, normalized)
        if source.get("type") != "file":
            raise FsError("EISDIR", f"illegal operation on a directory: {normalized}")

        if source.get("content_stored"):
            content = source.get("content", "")
        else:
            content = self._read_file_from_chunks(ctx, ph)

        data = content.encode("utf-8")
        if len(data) <= max_bytes:
            return ReadResult(content=content, truncated=False)
        truncated = data[:max_bytes].decode("utf-8", errors="ignore")
        return ReadResult(content=truncated, truncated=True)

    def find(self, request: FindRequest, cwd: str = "/") -> FindResult:
        root = normalize_virtual_path(request.root, cwd)
        root_stat = self.stat(request, root)
        limit = min(max(request.limit, 0), self.settings.osfs_max_search_hits)
        if limit == 0:
            return FindResult(entries=[], truncated=True)

        if root_stat.type == "file":
            if self._find_entry_matches(root_stat, request, root_depth=depth(root)):
                return FindResult(entries=[root_stat], truncated=False)
            return FindResult(entries=[], truncated=False)

        root_depth = depth(root)
        filters = self._base_filters(request) + [{"term": {"path_prefixes": root}}]
        if request.type:
            filters.append({"term": {"type": request.type}})

        body = {
            "size": min(1000, limit),
            "_source": ["path", "basename", "type", "size_bytes", "mtime", "child_count", "depth"],
            "query": {"bool": {"filter": filters}},
            "sort": [{"path": {"order": "asc"}}, {"path_hash": {"order": "asc"}}],
        }

        entries: list[Dirent] = []
        truncated = False
        while True:
            response = self.client.search(index=self.aliases["paths"], body=body)
            hits = response["hits"]["hits"]
            if not hits:
                break
            for hit in hits:
                source = hit["_source"]
                entry = self._dirent_from_source(source)
                if self._find_entry_matches(entry, request, root_depth=root_depth):
                    entries.append(entry)
                    if len(entries) >= limit:
                        truncated = True
                        break
            if truncated or len(hits) < body["size"]:
                break
            body["search_after"] = hits[-1]["sort"]

        return FindResult(entries=entries, truncated=truncated)

    def grep(self, ctx: RequestContext, pattern: str, roots: list[str], flags: GrepFlags, cwd: str = "/") -> GrepResult:
        if pattern == "":
            raise FsError("EINVAL", "grep pattern cannot be empty")

        limit = min(max(flags.limit, 0), self.settings.osfs_max_search_hits)
        if limit == 0:
            return GrepResult(hits=[], truncated=True)

        root_filters = self._root_filters(ctx, roots or ["/"], cwd)
        candidate_query = self._grep_candidate_query(pattern, flags)
        filters = self._base_filters(ctx)

        body = {
            "size": min(500, limit),
            "_source": ["path", "line_start", "content"],
            "query": {
                "bool": {
                    "filter": filters + [root_filters],
                    "must": [candidate_query],
                }
            },
            "sort": [{"path": {"order": "asc"}}, {"chunk_no": {"order": "asc"}}],
        }

        hits_out: list[GrepHit] = []
        seen_files: set[str] = set()
        matcher = self._line_matcher(pattern, flags)
        truncated = False

        while True:
            response = self.client.search(index=self.aliases["chunks"], body=body)
            hits = response["hits"]["hits"]
            if not hits:
                break

            for hit in hits:
                source = hit["_source"]
                path = source["path"]
                if not self._path_globs_match(path, flags.include_globs, flags.exclude_globs):
                    continue

                lines = source.get("content", "").split("\n")
                line_start = int(source.get("line_start", 1))
                for idx, line in enumerate(lines):
                    if not matcher(line):
                        continue
                    line_no = line_start + idx
                    if flags.files_with_matches:
                        if path in seen_files:
                            continue
                        seen_files.add(path)
                        hits_out.append(GrepHit(path=path, line_no=line_no, line=path))
                    else:
                        hits_out.append(GrepHit(path=path, line_no=line_no, line=line))
                    if len(hits_out) >= limit:
                        truncated = True
                        break
                if truncated:
                    break
            if truncated or len(hits) < body["size"]:
                break
            body["search_after"] = hits[-1]["sort"]

        return GrepResult(hits=hits_out, truncated=truncated)

    def _read_file_from_chunks(self, ctx: RequestContext, path_hash_value: str) -> str:
        body = {
            "size": 10000,
            "_source": ["chunk_no", "content"],
            "query": {
                "bool": {
                    "filter": self._base_filters(ctx) + [{"term": {"path_hash": path_hash_value}}]
                }
            },
            "sort": [{"chunk_no": {"order": "asc"}}],
        }
        response = self.client.search(index=self.aliases["chunks"], body=body)
        chunks = [hit["_source"].get("content", "") for hit in response["hits"]["hits"]]
        return "\n".join(chunks)

    def _root_filters(self, ctx: RequestContext, roots: list[str], cwd: str) -> dict:
        should: list[dict] = []
        for root in roots:
            normalized = normalize_virtual_path(root, cwd)
            stat = self.stat(ctx, normalized)
            if stat.type == "file":
                ph = path_hash(ctx.tenant_id, ctx.collection_id, normalized)
                should.append({"term": {"path_hash": ph}})
            else:
                should.append({"term": {"path_prefixes": normalized}})

        return {"bool": {"should": should, "minimum_should_match": 1}}

    def _grep_candidate_query(self, pattern: str, flags: GrepFlags) -> dict:
        if flags.regex:
            # Correctness first: Python does the final regex match line-by-line.
            # Avoid translating Python regex into Lucene regexp syntax incorrectly.
            return {"match_all": {}}

        value = "*" + _escape_wildcard(pattern) + "*"
        return {
            "wildcard": {
                "content_wildcard": {
                    "value": value,
                    "case_insensitive": flags.ignore_case,
                }
            }
        }

    def _line_matcher(self, pattern: str, flags: GrepFlags):
        if flags.regex:
            try:
                compiled = re.compile(pattern, re.IGNORECASE if flags.ignore_case else 0)
            except re.error as exc:
                raise FsError("EINVAL", f"invalid regex: {exc}")
            return lambda line: compiled.search(line) is not None

        if flags.ignore_case:
            lowered = pattern.lower()
            return lambda line: lowered in line.lower()
        return lambda line: pattern in line

    def _path_globs_match(self, path: str, include_globs: list[str], exclude_globs: list[str]) -> bool:
        normalized = path.lstrip("/")
        if include_globs:
            if not any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(normalized, g) for g in include_globs):
                return False
        if exclude_globs:
            if any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(normalized, g) for g in exclude_globs):
                return False
        return True

    def _find_entry_matches(self, entry: Dirent, request: FindRequest, root_depth: int) -> bool:
        if request.name_glob and not fnmatch.fnmatch(entry.basename, request.name_glob):
            return False
        if request.type and entry.type != request.type:
            return False
        if request.max_depth is not None:
            relative_depth = depth(entry.path) - root_depth
            if relative_depth > request.max_depth:
                return False
        return True

    def _base_filters(self, ctx: RequestContext) -> list[dict]:
        principals = ctx.principals or self.settings.default_principals
        return [
            {"term": {"tenant_id": ctx.tenant_id}},
            {"term": {"collection_id": ctx.collection_id}},
            {"terms": {"acl_principals": principals}},
        ]

    def _assert_acl(self, source: dict, principals: Iterable[str], path: str) -> None:
        allowed = set(source.get("acl_principals") or [])
        current = set(principals or self.settings.default_principals)
        if allowed and not (allowed & current):
            raise FsError("EACCES", f"permission denied: {path}", status_code=403)

    def _dirent_from_source(self, source: dict) -> Dirent:
        return Dirent(
            path=source["path"],
            basename=source.get("basename") or basename(source["path"]),
            type=source["type"],
            size_bytes=source.get("size_bytes"),
            mtime=source.get("mtime"),
            child_count=source.get("child_count"),
        )

    def _stat_from_source(self, source: dict) -> StatResponse:
        return StatResponse(
            path=source["path"],
            basename=source.get("basename") or basename(source["path"]),
            type=source["type"],
            size_bytes=source.get("size_bytes"),
            mtime=source.get("mtime"),
            child_count=source.get("child_count"),
            depth=source.get("depth", depth(source["path"])),
        )


def _escape_wildcard(value: str) -> str:
    return value.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")
