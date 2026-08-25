"""Durable audit run timeline queries."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from shadowops.application.ports import UnitOfWork
from shadowops.domain.errors import RunNotFoundError
from shadowops.domain.runs import TERMINAL_STATES, RunState, RunStep, StepStatus


@dataclass(frozen=True)
class TimelineEvent:
    version: int
    state: RunState
    at: datetime
    step_key: str | None = None
    attempt: int | None = None
    status: StepStatus | None = None
    handler_version: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class RunTimeline:
    run_id: UUID
    run_version: int
    terminal: bool
    events: list[TimelineEvent]
    current_step: RunStep | None


class RunTimelineService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def get(self, run_id: UUID, *, after_version: int = 0) -> RunTimeline:
        with self._uow_factory() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise RunNotFoundError(run_id)
            steps = uow.steps.list_for_run(run_id)
            current_step = uow.steps.get_current(run_id)

        events: list[TimelineEvent] = []
        if after_version < 1:
            events.append(
                TimelineEvent(
                    version=1,
                    state=RunState.QUEUED,
                    at=run.created_at or run.updated_at,
                )
            )
        completed_steps = sorted(
            (step for step in steps if step.resulting_run_version is not None),
            key=lambda step: step.resulting_run_version or 0,
        )
        events.extend(
            TimelineEvent(
                version=step.resulting_run_version,
                state=step.to_state,
                at=step.finished_at or run.updated_at,
                step_key=step.step_key,
                attempt=step.attempt,
                status=step.status,
                handler_version=step.handler_version,
                error_code=step.error_code,
            )
            for step in completed_steps
            if step.resulting_run_version is not None and step.resulting_run_version > after_version
        )
        return RunTimeline(
            run_id=run.id,
            run_version=run.version,
            terminal=run.state in TERMINAL_STATES,
            events=events,
            current_step=current_step,
        )
