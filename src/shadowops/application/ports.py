"""Application-facing persistence ports."""

from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from shadowops.domain.runs import AuditRun, OutboxEvent, RunStep


class RunRepository(Protocol):
    def add(self, run: AuditRun) -> None: ...

    def add_if_idempotency_absent(self, run: AuditRun) -> bool: ...

    def get(self, run_id: UUID) -> AuditRun | None: ...

    def get_by_idempotency_key(self, key: str) -> AuditRun | None: ...

    def save(self, run: AuditRun, *, expected_version: int) -> None: ...


class RunStepRepository(Protocol):
    def add(self, step: RunStep) -> None: ...


class OutboxRepository(Protocol):
    def add(self, event: OutboxEvent) -> None: ...

    def get(self, event_id: UUID) -> OutboxEvent | None: ...

    def lock_unpublished(self, *, now: datetime, limit: int) -> list[OutboxEvent]: ...


class UnitOfWork(Protocol):
    @property
    def runs(self) -> RunRepository: ...

    @property
    def steps(self) -> RunStepRepository: ...

    @property
    def outbox(self) -> OutboxRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
