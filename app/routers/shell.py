from fastapi import APIRouter, Depends

from app.deps import get_shell_service
from app.models import ShellRunRequest, ShellRunResponse
from app.shell import ShellService

router = APIRouter(prefix="/shell", tags=["shell"])


@router.post("/run", response_model=ShellRunResponse)
def run_shell(request: ShellRunRequest, shell: ShellService = Depends(get_shell_service)) -> ShellRunResponse:
    return shell.run(request)
