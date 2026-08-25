from datetime import UTC, datetime
from uuid import UUID

import pytest

from shadowops.application.run_timeline import RunTimelineService
from shadowops.domain.errors import RunNotFoundError
from shadowops.domain.runs import AuditRun, RunState, RunStep, StepStatus

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
CREATED_AT = datetime(2026, 8, 25, 2, 0, tzinfo=UTC)


class MemoryRunRepository:
    def __init__(self, run: AuditRun | None) -> None:
        self.run = run

    def get(self, run_id: UUID) -> AuditRun | None:
        return self.run if self.run is not None and self.run.id == run_id else None


class MemoryStepRepository:
    def __init__(self, steps: list[RunStep]) -> None:
        self.steps = steps

    def list_for_run(self, run_id: UUID) -> list[RunStep]:
        return [step for step in self.steps if step.run_id == run_id]

    def get_current(self, run_id: UUID) -> RunStep | None:
        return next(
            (
                step
                for step in self.steps
                if step.run_id == run_id and step.status is StepStatus.RUNNING
            ),
            None,
        )


class MemoryUnitOfWork:
    def __init__(self, run: AuditRun | None, steps: list[RunStep]) -> None:
        self.runs = MemoryRunRepository(run)
        self.steps = MemoryStepRepository(steps)

    def __enter__(self) -> "MemoryUnitOfWork":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _run(*, state: RunState = RunState.STATIC_ANALYSIS, version: int = 3) -> AuditRun:
    return AuditRun(
        id=RUN_ID,
        state=state,
        version=version,
        repository_path="projects/demo",
        idempotency_key="request-1",
        request_fingerprint="a" * 64,
        created_at=CREATED_AT,
        updated_at=datetime(2026, 8, 25, 2, 2, tzinfo=UTC),
    )


def _step(
    *,
    number: int,
    from_state: RunState,
    to_state: RunState,
    status: StepStatus = StepStatus.SUCCEEDED,
    resulting_version: int | None,
) -> RunStep:
    return RunStep(
        id=UUID(f"00000000-0000-4000-8000-{number:012d}"),
        run_id=RUN_ID,
        step_key=f"{from_state.value.lower()}-to-{to_state.value.lower()}",
        from_state=from_state,
        to_state=to_state,
        generation=1,
        attempt=1,
        status=status,
        expected_run_version=number,
        resulting_run_version=resulting_version,
        handler_version="m1.noop.v1",
        started_at=datetime(2026, 8, 25, 2, number, tzinfo=UTC),
        finished_at=(
            datetime(2026, 8, 25, 2, number, 30, tzinfo=UTC)
            if resulting_version is not None
            else None
        ),
    )


def test_timeline_contains_initial_run_and_only_durable_completed_steps() -> None:
    steps = [
        _step(
            number=1,
            from_state=RunState.QUEUED,
            to_state=RunState.DISCOVERING,
            resulting_version=2,
        ),
        _step(
            number=2,
            from_state=RunState.DISCOVERING,
            to_state=RunState.STATIC_ANALYSIS,
            resulting_version=3,
        ),
        _step(
            number=3,
            from_state=RunState.STATIC_ANALYSIS,
            to_state=RunState.PLANNING,
            status=StepStatus.RUNNING,
            resulting_version=None,
        ),
    ]
    service = RunTimelineService(lambda: MemoryUnitOfWork(_run(), steps))

    timeline = service.get(RUN_ID)

    assert [(event.version, event.state) for event in timeline.events] == [
        (1, RunState.QUEUED),
        (2, RunState.DISCOVERING),
        (3, RunState.STATIC_ANALYSIS),
    ]
    assert timeline.events[1].step_key == "queued-to-discovering"
    assert timeline.events[1].status is StepStatus.SUCCEEDED
    assert timeline.current_step is steps[2]
    assert timeline.run_version == 3
    assert timeline.terminal is False


def test_timeline_after_version_is_an_exclusive_resume_cursor() -> None:
    steps = [
        _step(
            number=1,
            from_state=RunState.QUEUED,
            to_state=RunState.DISCOVERING,
            resulting_version=2,
        ),
        _step(
            number=2,
            from_state=RunState.DISCOVERING,
            to_state=RunState.COMPLETED,
            resulting_version=3,
        ),
    ]
    service = RunTimelineService(lambda: MemoryUnitOfWork(_run(state=RunState.COMPLETED), steps))

    timeline = service.get(RUN_ID, after_version=1)

    assert [event.version for event in timeline.events] == [2, 3]
    assert timeline.terminal is True
    assert timeline.current_step is None


def test_timeline_rejects_unknown_run() -> None:
    service = RunTimelineService(lambda: MemoryUnitOfWork(None, []))

    with pytest.raises(RunNotFoundError):
        service.get(RUN_ID)
