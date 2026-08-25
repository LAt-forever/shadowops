"""Audit run command and query routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from shadowops.api.schemas.runs import (
    AuditRunViewV1,
    CancelAuditRunRequestV1,
    CreateAuditRunRequestV1,
)
from shadowops.application.runs import RunService
from shadowops.domain.errors import (
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    RunNotFoundError,
    TerminalRunError,
)
from shadowops.domain.runs import AuditRun

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def _service(request: Request) -> RunService:
    service: RunService | None = request.app.state.run_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "RUN_SERVICE_UNAVAILABLE"},
        )
    return service


def _view(run: AuditRun) -> AuditRunViewV1:
    base = f"/api/v1/runs/{run.id}"
    return AuditRunViewV1(
        id=run.id,
        state=run.state,
        version=run.version,
        cancel_requested_at=run.cancel_requested_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        completed_at=run.completed_at,
        links={"self": base, "events": f"{base}/events", "timeline": f"{base}/timeline"},
    )


@router.post("", response_model=AuditRunViewV1, status_code=status.HTTP_202_ACCEPTED)
def create_run(
    payload: CreateAuditRunRequestV1,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> AuditRunViewV1:
    try:
        run = _service(request).create(payload, idempotency_key=idempotency_key)
    except IdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": error.code}
        ) from error
    response.headers["Location"] = f"/api/v1/runs/{run.id}"
    return _view(run)


@router.get("/{run_id}", response_model=AuditRunViewV1)
def get_run(run_id: UUID, request: Request) -> AuditRunViewV1:
    try:
        run = _service(request).get(run_id)
    except RunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": error.code}
        ) from error
    return _view(run)


@router.post(
    "/{run_id}/cancel",
    response_model=AuditRunViewV1,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_run(run_id: UUID, payload: CancelAuditRunRequestV1, request: Request) -> AuditRunViewV1:
    try:
        run = _service(request).cancel(run_id, expected_version=payload.expected_version)
    except RunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": error.code}
        ) from error
    except (OptimisticConcurrencyError, TerminalRunError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": error.code}
        ) from error
    return _view(run)
