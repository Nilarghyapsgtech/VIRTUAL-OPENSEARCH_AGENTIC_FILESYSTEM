from __future__ import annotations

from fastapi import APIRouter, Depends
from opensearchpy import OpenSearch

from app.config import Settings, get_settings
from app.deps import get_opensearch_client
from app.ingest import ingest_json, ingest_local
from app.mappings import ensure_indices, reset_indices
from app.models import BootstrapResponse, IngestJsonRequest, IngestLocalRequest, IngestResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/bootstrap", response_model=BootstrapResponse)
def bootstrap(
    client: OpenSearch = Depends(get_opensearch_client),
    settings: Settings = Depends(get_settings),
) -> BootstrapResponse:
    indices, alias_map = ensure_indices(client, settings)
    return BootstrapResponse(ok=True, indices=indices, aliases=alias_map)


@router.post("/reset")
def reset(
    client: OpenSearch = Depends(get_opensearch_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    reset_indices(client, settings)
    return {"ok": True}


@router.post("/ingest/json", response_model=IngestResponse)
def ingest_json_endpoint(
    request: IngestJsonRequest,
    client: OpenSearch = Depends(get_opensearch_client),
    settings: Settings = Depends(get_settings),
) -> IngestResponse:
    return ingest_json(client, settings, request)


@router.post("/ingest/local", response_model=IngestResponse)
def ingest_local_endpoint(
    request: IngestLocalRequest,
    client: OpenSearch = Depends(get_opensearch_client),
    settings: Settings = Depends(get_settings),
) -> IngestResponse:
    return ingest_local(client, settings, request)
