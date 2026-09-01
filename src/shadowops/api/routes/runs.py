"""Audit run command and query routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from shadowops.agent.contracts import AuditPlanRecordV1
from shadowops.api.schemas.runs import (
    AuditRunViewV1,
    CancelAuditRunRequestV1,
    CreateAuditRunRequestV1,
)
from shadowops.application.dynamic_results import DynamicAuditQueryService
from shadowops.application.planning import AuditPlanQueryService
from shadowops.application.runs import RunService
from shadowops.application.static_analysis import StaticReportQueryService
from shadowops.domain.errors import (
    AuditPlanNotReadyError,
    DynamicAuditNotReadyError,
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    RunNotFoundError,
    StaticReportNotReadyError,
    TerminalRunError,
)
from shadowops.domain.runs import AuditRun
from shadowops.rules.contracts import StaticReportV1
from shadowops.sandbox.contracts import DynamicAuditViewV1

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def _service(request: Request) -> RunService:
    service: RunService | None = request.app.state.run_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "RUN_SERVICE_UNAVAILABLE"},
        )
    return service


def _report_service(request: Request) -> StaticReportQueryService:
    service: StaticReportQueryService | None = request.app.state.static_report_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "STATIC_REPORT_SERVICE_UNAVAILABLE"},
        )
    return service


def _plan_service(request: Request) -> AuditPlanQueryService:
    service: AuditPlanQueryService | None = request.app.state.plan_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUDIT_PLAN_SERVICE_UNAVAILABLE"},
        )
    return service


def _dynamic_service(request: Request) -> DynamicAuditQueryService:
    service: DynamicAuditQueryService | None = request.app.state.dynamic_audit_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "DYNAMIC_AUDIT_SERVICE_UNAVAILABLE"},
        )
    return service


def _view(run: AuditRun) -> AuditRunViewV1:
    base = f"/api/v1/runs/{run.id}"
    return AuditRunViewV1(
        id=run.id,
        state=run.state,
        version=run.version,
        execution_profile="m5.dynamic-evidence.v1",
        failure_code=run.failure_code,
        cancel_requested_at=run.cancel_requested_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        completed_at=run.completed_at,
        links={
            "self": base,
            "events": f"{base}/events",
            "timeline": f"{base}/timeline",
            "static_report": f"{base}/static-report",
            "plan": f"{base}/plan",
            "dynamic_result": f"{base}/dynamic-result",
        },
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


@router.get("/{run_id}/static-report", response_model=StaticReportV1)
def get_static_report(run_id: UUID, request: Request) -> StaticReportV1:
    try:
        return _report_service(request).get(run_id)
    except RunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": error.code}
        ) from error
    except StaticReportNotReadyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": error.code}
        ) from error


@router.get("/{run_id}/plan", response_model=AuditPlanRecordV1)
def get_audit_plan(run_id: UUID, request: Request) -> AuditPlanRecordV1:
    try:
        return _plan_service(request).get(run_id)
    except RunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": error.code}
        ) from error
    except AuditPlanNotReadyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": error.code}
        ) from error


@router.get("/{run_id}/dynamic-result", response_model=DynamicAuditViewV1)
def get_dynamic_result(run_id: UUID, request: Request) -> DynamicAuditViewV1:
    try:
        return _dynamic_service(request).get(run_id)
    except RunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": error.code}
        ) from error
    except DynamicAuditNotReadyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": error.code}
        ) from error


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
