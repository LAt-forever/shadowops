from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID

from shadowops.domain.runs import AuditRun, OutboxEvent, RunState, RunStep, StepStatus
from shadowops.worker.reconciler import ReconcileResult, RunReconciler

NOW = datetime(2026, 8, 25, 5, 0, tzinfo=UTC)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
EVENT_ID = UUID("22222222-2222-4222-8222-222222222222")
FAILURE_STEP_ID = UUID("33333333-3333-4333-8333-333333333333")


class MemoryRunRepository:
    def __init__(self, run: AuditRun) -> None:
        self.run = run

    def get(self, run_id: UUID) -> AuditRun | None:
        return deepcopy(self.run) if run_id == self.run.id else None

    def save(self, run: AuditRun, *, expected_version: int) -> None:
        assert self.run.version == expected_version
        self.run = deepcopy(run)


class MemoryStepRepository:
    def __init__(self) -> None:
        self.steps: list[RunStep] = []

    def add(self, step: RunStep) -> None:
        self.steps.append(deepcopy(step))


class MemoryOutboxRepository:
    def __init__(self, events: list[OutboxEvent]) -> None:
        self.events = events

    def lock_stale_deliveries(
        self, *, now: datetime, stale_before: datetime, limit: int
    ) -> list[OutboxEvent]:
        return [deepcopy(event) for event in self.events[:limit]]

    def reopen(self, event_id: UUID, *, available_at: datetime, reason: str) -> bool:
        event = next(item for item in self.events if item.id == event_id)
        event.published_at = None
        event.available_at = available_at
        event.last_error = reason
        return True


class MemoryUnitOfWork:
    def __init__(
        self,
        runs: MemoryRunRepository,
        steps: MemoryStepRepository,
        outbox: MemoryOutboxRepository,
    ) -> None:
        self.runs = runs
        self.steps = steps
        self.outbox = outbox
        self.committed = False

    def __enter__(self) -> "MemoryUnitOfWork":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


def _run() -> AuditRun:
    return AuditRun(
        id=RUN_ID,
        state=RunState.QUEUED,
        version=1,
        repository_path="projects/demo",
        idempotency_key="request-1",
        request_fingerprint="a" * 64,
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(minutes=1),
    )


def _event(attempts: int) -> OutboxEvent:
    return OutboxEvent(
        id=EVENT_ID,
        aggregate_id=RUN_ID,
        aggregate_version=1,
        topic="run.advance.requested.v1",
        payload={"run_id": str(RUN_ID), "expected_state": "QUEUED", "expected_version": 1},
        available_at=NOW - timedelta(minutes=1),
        published_at=NOW - timedelta(seconds=20),
        publish_attempts=attempts,
        created_at=NOW - timedelta(minutes=1),
    )


def _reconciler(event: OutboxEvent, max_attempts: int = 3):
    runs = MemoryRunRepository(_run())
    steps = MemoryStepRepository()
    outbox = MemoryOutboxRepository([event])
    uow = MemoryUnitOfWork(runs, steps, outbox)
    reconciler = RunReconciler(
        lambda: uow,
        clock=lambda: NOW,
        uuid_factory=lambda: FAILURE_STEP_ID,
        stale_after=timedelta(seconds=10),
        max_attempts=max_attempts,
    )
    return reconciler, uow


def test_reconciler_reopens_a_stale_delivery_within_budget() -> None:
    event = _event(attempts=1)
    reconciler, uow = _reconciler(event)

    result = reconciler.reconcile_batch(limit=10)

    assert result == ReconcileResult(reopened=1, failed=0)
    assert event.published_at is None
    assert event.available_at == NOW
    assert event.last_error == "RECOVERED_STALE_DELIVERY"
    assert uow.runs.run.state is RunState.QUEUED
    assert uow.committed is True


def test_reconciler_fails_a_run_after_recovery_budget_is_exhausted() -> None:
    event = _event(attempts=3)
    reconciler, uow = _reconciler(event, max_attempts=3)

    result = reconciler.reconcile_batch(limit=10)

    assert result == ReconcileResult(reopened=0, failed=1)
    assert uow.runs.run.state is RunState.FAILED
    assert uow.runs.run.version == 2
    assert uow.runs.run.failure_code == "RECOVERY_EXHAUSTED"
    assert len(uow.steps.steps) == 1
    assert uow.steps.steps[0].status is StepStatus.FAILED
    assert uow.steps.steps[0].resulting_run_version == 2
