.PHONY: up down bootstrap ingest demo test

up:
	docker compose up -d --build

down:
	docker compose down

bootstrap:
	curl -s -X POST http://localhost:8000/admin/bootstrap | python -m json.tool

ingest:
	curl -s -X POST http://localhost:8000/admin/ingest/local \
	  -H 'content-type: application/json' \
	  -d '{"tenant_id":"demo","collection_id":"docs","root_dir":"/data/ingest/sample_docs","principals":["group:public"]}' | python -m json.tool

demo:
	curl -s -X POST http://localhost:8000/shell/run \
	  -H 'content-type: application/json' \
	  -d '{"tenant_id":"demo","collection_id":"docs","session_id":"demo-1","principals":["group:public"],"command":"grep -rin \"OAuth\" /docs | head -20"}' | python -m json.tool

test:
	pytest -q
