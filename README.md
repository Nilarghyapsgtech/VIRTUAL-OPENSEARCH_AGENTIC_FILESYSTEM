
Agent-facing virtual filesystem over OpenSearch.
<img width="8191" height="2777" alt="image" src="https://github.com/user-attachments/assets/c7c70ee2-b35c-44a4-8012-3e224bf2f2b4" />



The agent sends bash-like commands to a FastAPI service. The service does not execute real bash. It parses a restricted command set and translates read-only filesystem operations into OpenSearch reads and searches.

## Implemented architecture

```text
AI Agent
  -> POST /shell/run
      -> Restricted shell parser
          -> Command router
              pwd/cd/ls/find -> osfs-path-nodes-current
              cat/head/tail   -> osfs-files-current + osfs-file-chunks-current
              grep/rg         -> osfs-file-chunks-current + local line matcher
              write commands  -> EROFS
                  -> OpenSearch
```

## Features

- FastAPI service with `/shell`, `/fs`, and `/admin` routes.
- OpenSearch index mappings for files, line chunks, path nodes, and sessions.
- JSON ingestion and local-directory ingestion.
- Read-only virtual filesystem semantics.
- Commands: `pwd`, `cd`, `ls`, `cat`, `head`, `tail`, `find`, `grep`, `rg`, `stat`, `wc -l`, `sort`.
- Simple pipelines such as `grep -rin "OAuth" /docs | head -20`.
- POSIX-like errors: `ENOENT`, `EISDIR`, `ENOTDIR`, `EACCES`, `EROFS`.
- Tenant, collection, and ACL filters on every OpenSearch query.

## Project layout

```text
app/
  main.py                  FastAPI entrypoint
  config.py                Environment settings
  opensearch_client.py     OpenSearch client factory
  mappings.py              Index mappings, aliases, bootstrap/reset
  ingest.py                JSON and local filesystem ingestion
  fs.py                    OpenSearchFS read-only filesystem interface
  shell.py                 Restricted shell and pipeline engine
  path_utils.py            POSIX path normalization and hashing
  routers/
    admin.py               Bootstrap, reset, ingest endpoints
    fs.py                  Direct filesystem endpoints
    shell.py               Agent shell endpoint
sample_docs/               Demo docs to ingest
tests/                     Parser and path utility tests
```

## Run locally

```bash
cp .env.example .env
docker compose up -d --build
```

Bootstrap indices:

```bash
curl -s -X POST http://localhost:8000/admin/bootstrap | python -m json.tool
```

Ingest sample docs:

```bash
curl -s -X POST http://localhost:8000/admin/ingest/local \
  -H 'content-type: application/json' \
  -d '{
    "tenant_id":"demo",
    "collection_id":"docs",
    "root_dir":"/data/ingest/sample_docs",
    "principals":["group:public"],
    "default_acl_principals":["group:public"],
    "replace_collection":true
  }' | python -m json.tool
```

Run agent-style commands:

```bash
curl -s -X POST http://localhost:8000/shell/run \
  -H 'content-type: application/json' \
  -d '{
    "tenant_id":"demo",
    "collection_id":"docs",
    "session_id":"demo-1",
    "principals":["group:public"],
    "command":"grep -rin \"OAuth\" /docs | head -20"
  }' | python -m json.tool
```

Example response:

```json
{
  "session_id": "demo-1",
  "cwd": "/",
  "stdout": "/docs/auth/oauth.mdx:3:OAuth is used for delegated authorization.\n/docs/auth/oauth.mdx:7:2. Configure the OAuth redirect URI.\n/docs/auth/oauth.mdx:13:OAuth scopes are validated before token exchange.\n/docs/auth/basic-auth.mdx:5:Prefer OAuth for user-facing integrations.\n",
  "stderr": "",
  "exit_code": 0,
  "truncated": false
}
```

## Direct filesystem API

List a directory:

```bash
curl -s -X POST http://localhost:8000/fs/list \
  -H 'content-type: application/json' \
  -d '{"tenant_id":"demo","collection_id":"docs","principals":["group:public"],"path":"/docs"}' | python -m json.tool
```

Read a file:

```bash
curl -s -X POST http://localhost:8000/fs/cat \
  -H 'content-type: application/json' \
  -d '{"tenant_id":"demo","collection_id":"docs","principals":["group:public"],"path":"/docs/auth/oauth.mdx"}' | python -m json.tool
```

Find files:

```bash
curl -s -X POST http://localhost:8000/fs/find \
  -H 'content-type: application/json' \
  -d '{"tenant_id":"demo","collection_id":"docs","principals":["group:public"],"root":"/","name_glob":"*.mdx","type":"file"}' | python -m json.tool
```

Grep:

```bash
curl -s -X POST http://localhost:8000/fs/grep \
  -H 'content-type: application/json' \
  -d '{
    "tenant_id":"demo",
    "collection_id":"docs",
    "principals":["group:public"],
    "pattern":"OAuth",
    "roots":["/docs"],
    "flags":{"recursive":true,"ignore_case":true,"line_numbers":true,"limit":20}
  }' | python -m json.tool
```

## Data model

### `osfs-files-current`

One document per file. Stores metadata, ACLs, path fields, content hash, and small-file content.

### `osfs-file-chunks-current`

One document per line chunk. Used for scalable `grep`, `rg`, and large-file reads.

### `osfs-path-nodes-current`

One document per path node. Used for `ls`, `cd`, `find`, and `stat`.

### `osfs-sessions-current`

Mapping is included for durable sessions. This implementation keeps shell sessions in memory for simplicity. Use this index if you need multi-replica session persistence.

## Security notes

The local Docker Compose file disables the OpenSearch security plugin to keep the demo simple. Do not use that setting in production.

Production deployment should use:

- TLS-enabled OpenSearch.
- A read-only OpenSearch role for the FastAPI query service.
- A separate write-capable role for the indexer.
- Authentication at the FastAPI layer.
- Principals derived from verified identity, not from request body.
- ACL filters on every query.
- Audit logging of shell commands and generated OpenSearch queries.
- Output limits and query timeouts.

## Development

Run tests outside Docker:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

Run the API without Docker, pointing at an existing OpenSearch cluster:

```bash
export OPENSEARCH_URL=http://localhost:9200
uvicorn app.main:app --reload
```
