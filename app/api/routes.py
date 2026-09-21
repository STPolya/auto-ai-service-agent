"""HTTP input/output only. Sync routes run in FastAPI's worker thread pool."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.dependencies import require_admin_key
from app.api.schemas import StatusUpdateRequest, SupportRequestDetailResponse, SupportRequestResponse
from app.services import support_request_service as service
from app.services.support_status import SupportStatus

router = APIRouter(prefix="/api/admin", tags=["Admin support requests"], dependencies=[Depends(require_admin_key)])


@router.get("/support-requests", response_model=list[SupportRequestResponse])
def list_support_requests(
    status: SupportStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=service.MAX_ADMIN_PAGE_SIZE)] = service.DEFAULT_ADMIN_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return service.list_support_requests(status=status.value if status is not None else None, limit=limit, offset=offset)


@router.get("/support-requests/{request_id}", response_model=SupportRequestDetailResponse)
def support_request_detail(request_id: Annotated[int, Path(ge=1)]):
    return service.get_support_request_detail(request_id)


@router.patch("/support-requests/{request_id}/status", response_model=SupportRequestResponse)
def update_support_request_status(request_id: Annotated[int, Path(ge=1)], body: StatusUpdateRequest):
    return service.update_support_request_status(request_id, body.status.value)
