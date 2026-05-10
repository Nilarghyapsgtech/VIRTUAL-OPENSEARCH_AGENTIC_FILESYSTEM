from __future__ import annotations

from opensearchpy import OpenSearch

from app.config import Settings


def concrete_indices(settings: Settings) -> dict[str, str]:
    prefix = settings.osfs_index_prefix
    return {
        "files": f"{prefix}-files-v1",
        "chunks": f"{prefix}-file-chunks-v1",
        "paths": f"{prefix}-path-nodes-v1",
        "sessions": f"{prefix}-sessions-v1",
    }


def aliases(settings: Settings) -> dict[str, str]:
    prefix = settings.osfs_index_prefix
    return {
        "files": f"{prefix}-files-current",
        "chunks": f"{prefix}-file-chunks-current",
        "paths": f"{prefix}-path-nodes-current",
        "sessions": f"{prefix}-sessions-current",
    }


def file_index_body() -> dict:
    return {
        "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
        "mappings": {
            "dynamic": False,
            "properties": {
                "tenant_id": {"type": "keyword"},
                "collection_id": {"type": "keyword"},
                "path_hash": {"type": "keyword"},
                "path": {"type": "keyword", "fields": {"text": {"type": "text"}}},
                "path_prefixes": {"type": "keyword"},
                "parent_path": {"type": "keyword"},
                "basename": {"type": "keyword", "fields": {"text": {"type": "text"}}},
                "extension": {"type": "keyword"},
                "type": {"type": "keyword"},
                "language": {"type": "keyword"},
                "size_bytes": {"type": "long"},
                "mtime": {"type": "date"},
                "content_sha256": {"type": "keyword"},
                "content": {"type": "text", "index_options": "offsets"},
                "content_stored": {"type": "boolean"},
                "acl_principals": {"type": "keyword"},
                "source_uri": {"type": "keyword", "index": False},
            },
        },
    }


def chunk_index_body() -> dict:
    return {
        "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
        "mappings": {
            "dynamic": False,
            "properties": {
                "tenant_id": {"type": "keyword"},
                "collection_id": {"type": "keyword"},
                "path_hash": {"type": "keyword"},
                "chunk_id": {"type": "keyword"},
                "path": {"type": "keyword"},
                "path_prefixes": {"type": "keyword"},
                "basename": {"type": "keyword"},
                "extension": {"type": "keyword"},
                "chunk_no": {"type": "integer"},
                "line_start": {"type": "integer"},
                "line_end": {"type": "integer"},
                "content": {"type": "text"},
                "content_wildcard": {"type": "wildcard"},
                "acl_principals": {"type": "keyword"},
                "content_sha256": {"type": "keyword"},
            },
        },
    }


def path_index_body() -> dict:
    return {
        "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
        "mappings": {
            "dynamic": False,
            "properties": {
                "tenant_id": {"type": "keyword"},
                "collection_id": {"type": "keyword"},
                "path_hash": {"type": "keyword"},
                "path": {"type": "keyword"},
                "path_prefixes": {"type": "keyword"},
                "parent_path": {"type": "keyword"},
                "basename": {"type": "keyword"},
                "type": {"type": "keyword"},
                "depth": {"type": "integer"},
                "child_count": {"type": "integer"},
                "size_bytes": {"type": "long"},
                "mtime": {"type": "date"},
                "acl_principals": {"type": "keyword"},
            },
        },
    }


def session_index_body() -> dict:
    return {
        "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
        "mappings": {
            "dynamic": False,
            "properties": {
                "session_id": {"type": "keyword"},
                "tenant_id": {"type": "keyword"},
                "collection_id": {"type": "keyword"},
                "cwd": {"type": "keyword"},
                "principals": {"type": "keyword"},
                "updated_at": {"type": "date"},
            },
        },
    }


def ensure_indices(client: OpenSearch, settings: Settings) -> tuple[dict[str, str], dict[str, str]]:
    indices = concrete_indices(settings)
    index_bodies = {
        indices["files"]: file_index_body(),
        indices["chunks"]: chunk_index_body(),
        indices["paths"]: path_index_body(),
        indices["sessions"]: session_index_body(),
    }

    for index, body in index_bodies.items():
        if not client.indices.exists(index=index):
            client.indices.create(index=index, body=body)

    alias_map = aliases(settings)
    for key, alias in alias_map.items():
        concrete = indices[key]
        if not _alias_points_to(client, alias, concrete):
            _replace_alias(client, alias, concrete)

    return indices, alias_map


def _alias_points_to(client: OpenSearch, alias: str, concrete_index: str) -> bool:
    try:
        result = client.indices.get_alias(name=alias)
    except Exception:
        return False
    return concrete_index in result


def _replace_alias(client: OpenSearch, alias: str, concrete_index: str) -> None:
    actions: list[dict] = []
    try:
        existing = client.indices.get_alias(name=alias)
        for index_name in existing.keys():
            actions.append({"remove": {"index": index_name, "alias": alias}})
    except Exception:
        pass

    actions.append({"add": {"index": concrete_index, "alias": alias}})
    client.indices.update_aliases(body={"actions": actions})


def reset_indices(client: OpenSearch, settings: Settings) -> None:
    for index in concrete_indices(settings).values():
        client.indices.delete(index=index, ignore=[400, 404])
