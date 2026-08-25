"""Durable timeline and server-sent event routes."""

import asyncio
from collections.abc import AsyncIterator
from time import monotonic
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from shadowops.api.schemas.runs import (
    RunStepViewV1,
    RunTimelineEventV1,
    RunTimelineViewV1,
)
from shadowops.application.run_timeline import RunTimeline, RunTimelineService, TimelineEvent
from shadowops.domain.errors import RunNotFoundError

router = APIRouter(prefix="/api/v1/runs", tags=["run-events"])


def _service(request: Request) -> RunTimelineService:
    service: RunTimelineService | None = request.app.state.timeline_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "RUN_TIMELINE_SERVICE_UNAVAILABLE"},
        )
    return service


def _event_view(event: TimelineEvent) -> RunTimelineEventV1:
    return RunTimelineEventV1(
        version=event.version,
        state=event.state,
        at=event.at,
        step_key=event.step_key,
        attempt=event.attempt,
        status=event.status,
        handler_version=event.handler_version,
        error_code=event.error_code,
    )


def _timeline_view(timeline: RunTimeline) -> RunTimelineViewV1:
    current = timeline.current_step
    return RunTimelineViewV1(
        run_id=timeline.run_id,
        run_version=timeline.run_version,
        terminal=timeline.terminal,
        events=[_event_view(event) for event in timeline.events],
        current_step=(
            None
            if current is None
            else RunStepViewV1(
                step_key=current.step_key,
                attempt=current.attempt,
                status=current.status,
                handler_version=current.handler_version,
                started_at=current.started_at,
                heartbeat_at=current.heartbeat_at,
                lease_expires_at=current.lease_expires_at,
            )
        ),
    )


def _not_found(error: RunNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": error.code},
    )


@router.get("/{run_id}/timeline", response_model=RunTimelineViewV1)
def get_timeline(run_id: UUID, request: Request) -> RunTimelineViewV1:
    try:
        timeline = _service(request).get(run_id)
    except RunNotFoundError as error:
        raise _not_found(error) from error
    return _timeline_view(timeline)


def _parse_cursor(value: str | None) -> int:
    if value is None:
        return 0
    try:
        cursor = int(value)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_EVENT_CURSOR"},
        ) from error
    if cursor < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_EVENT_CURSOR"},
        )
    return cursor


def _sse_event(event: TimelineEvent) -> str:
    payload = _event_view(event).model_dump_json()
    return f"id: {event.version}\nevent: run.state.changed\ndata: {payload}\n\n"


@router.get("/{run_id}/events", response_class=StreamingResponse)
async def stream_events(
    run_id: UUID,
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    cursor = _parse_cursor(last_event_id)
    service = _service(request)
    try:
        initial = await run_in_threadpool(service.get, run_id, after_version=cursor)
    except RunNotFoundError as error:
        raise _not_found(error) from error

    async def generate() -> AsyncIterator[str]:
        nonlocal cursor
        timeline = initial
        last_output = monotonic()
        while True:
            for event in timeline.events:
                cursor = event.version
                last_output = monotonic()
                yield _sse_event(event)
            if timeline.terminal and cursor >= timeline.run_version:
                return
            if await request.is_disconnected():
                return
            if monotonic() - last_output >= request.app.state.sse_keepalive_seconds:
                last_output = monotonic()
                yield ": keepalive\n\n"
            await asyncio.sleep(request.app.state.sse_poll_interval_seconds)
            timeline = await run_in_threadpool(service.get, run_id, after_version=cursor)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
