from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import FsError
from app.opensearch_client import get_opensearch_client
from app.routers import admin, fs, shell

app = FastAPI(
    title="OpenSearchFS for Agents",
    version="0.1.0",
    description="Read-only agent-facing virtual filesystem over OpenSearch.",
)


@app.exception_handler(FsError)
def handle_fs_error(request: Request, exc: FsError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


@app.get("/health")
def health() -> dict:
    client = get_opensearch_client()
    return {"ok": True, "opensearch": client.ping()}


app.include_router(admin.router)
app.include_router(fs.router)
app.include_router(shell.router)
