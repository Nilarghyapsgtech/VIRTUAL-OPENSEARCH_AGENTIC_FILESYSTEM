from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.deps import get_fs
from app.fs import OpenSearchFS
from app.models import (
    CatResponse,
    FindRequest,
    FindResponse,
    GrepRequest,
    GrepResponse,
    ListResponse,
    RequestContext,
    StatResponse,
)

router = APIRouter(prefix="/fs", tags=["filesystem"])


class PathRequest(RequestContext):
    path: str = "/"
    cwd: str = "/"


class ReadRequest(PathRequest):
    max_bytes: int | None = None


@router.post("/stat", response_model=StatResponse)
def stat(request: PathRequest, fs: OpenSearchFS = Depends(get_fs)) -> StatResponse:
    return fs.stat(request, request.path, cwd=request.cwd)


@router.post("/list", response_model=ListResponse)
def list_dir(request: PathRequest, fs: OpenSearchFS = Depends(get_fs)) -> ListResponse:
    entries = fs.readdir(request, request.path, cwd=request.cwd)
    return ListResponse(path=request.path, entries=entries)


@router.post("/cat", response_model=CatResponse)
def cat(request: ReadRequest, fs: OpenSearchFS = Depends(get_fs)) -> CatResponse:
    result = fs.read_file(request, request.path, cwd=request.cwd, max_bytes=request.max_bytes)
    return CatResponse(path=request.path, content=result.content, truncated=result.truncated)


@router.post("/find", response_model=FindResponse)
def find(request: FindRequest, fs: OpenSearchFS = Depends(get_fs)) -> FindResponse:
    result = fs.find(request)
    return FindResponse(entries=result.entries, truncated=result.truncated)


@router.post("/grep", response_model=GrepResponse)
def grep(request: GrepRequest, fs: OpenSearchFS = Depends(get_fs)) -> GrepResponse:
    result = fs.grep(request, request.pattern, request.roots, request.flags)
    return GrepResponse(hits=result.hits, truncated=result.truncated)
