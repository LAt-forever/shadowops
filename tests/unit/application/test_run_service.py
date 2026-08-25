from datetime import UTC, datetime
from uuid import UUID

import pytest

from shadowops.api.schemas.runs import CreateAuditRunRequestV1
from shadowops.application.runs import RunService
from shadowops.domain.errors import IdempotencyConflictError, RunNotFoundError
from shadowops.domain.runs import AuditRun, OutboxEvent, RunState


class MemoryRunRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, AuditRun] = {}

    def add_if_idempotency_absent(self, run: AuditRun) -> bool:
        if self.get_by_idempotency_key(run.idempotency_key) is not None:
            return False
        self.by_id[run.id] = run
        return True

    def get(self, run_id: UUID) -> AuditRun | None:
        return self.by_id.get(run_id)

    def get_by_idempotency_key(self, key: str) -> AuditRun | None:
        return next((run for run in self.by_id.values() if run.idempotency_key == key), None)


class MemoryOutboxRepository:
    def __init__(self) -> None:
        self.events: list[OutboxEvent] = []

    def add(self, event: OutboxEvent) -> None:
        self.events.append(event)


class MemoryUnitOfWork:
    def __init__(self) -> None:
        self.runs = MemoryRunRepository()
        self.outbox = MemoryOutboxRepository()
        self.commits = 0

    def __enter__(self) -> "MemoryUnitOfWork":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1


def _service(uow: MemoryUnitOfWork) -> RunService:
    ids = iter(
        [
            UUID("11111111-1111-4111-8111-111111111111"),
            UUID("22222222-2222-4222-8222-222222222222"),
        ]
    )
    return RunService(
        lambda: uow,
        clock=lambda: datetime(2026, 8, 25, 2, 0, tzinfo=UTC),
        uuid_factory=lambda: next(ids),
    )


def test_create_run_atomically_records_queued_run_and_initial_event() -> None:
    uow = MemoryUnitOfWork()
    service = _service(uow)

    run = service.create(
        CreateAuditRunRequestV1(repository_path="projects/demo"),
        idempotency_key="request-1",
    )

    assert run.id == UUID("11111111-1111-4111-8111-111111111111")
    assert run.state is RunState.QUEUED
    assert run.version == 1
    assert run.idempotency_key == "request-1"
    assert uow.runs.by_id == {run.id: run}
    assert len(uow.outbox.events) == 1
    assert uow.outbox.events[0].aggregate_id == run.id
    assert uow.outbox.events[0].payload == {
        "run_id": str(run.id),
        "expected_state": "QUEUED",
        "expected_version": 1,
    }
    assert uow.commits == 1


def test_same_idempotency_key_and_request_returns_the_existing_run() -> None:
    uow = MemoryUnitOfWork()
    service = _service(uow)
    request = CreateAuditRunRequestV1(repository_path="projects/demo")

    first = service.create(request, idempotency_key="request-1")
    replay = service.create(request, idempotency_key="request-1")

    assert replay is first
    assert len(uow.runs.by_id) == 1
    assert len(uow.outbox.events) == 1
    assert uow.commits == 1


def test_same_idempotency_key_with_different_request_is_rejected() -> None:
    uow = MemoryUnitOfWork()
    service = _service(uow)
    service.create(
        CreateAuditRunRequestV1(repository_path="projects/first"),
        idempotency_key="request-1",
    )

    with pytest.raises(IdempotencyConflictError) as error:
        service.create(
            CreateAuditRunRequestV1(repository_path="projects/second"),
            idempotency_key="request-1",
        )

    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert len(uow.runs.by_id) == 1
    assert len(uow.outbox.events) == 1


def test_get_run_rejects_an_unknown_identifier() -> None:
    service = _service(MemoryUnitOfWork())
    missing_id = UUID("33333333-3333-4333-8333-333333333333")

    with pytest.raises(RunNotFoundError) as error:
        service.get(missing_id)

    assert error.value.code == "RUN_NOT_FOUND"
