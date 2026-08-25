"""Application-facing persistence ports."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from shadowops.domain.runs import AuditRun, OutboxEvent, RunStep


class RunRepository(Protocol):
    def add(self, run: AuditRun) -> None: ...

    def get(self, run_id: UUID) -> AuditRun | None: ...

    def save(self, run: AuditRun, *, expected_version: int) -> None: ...


class RunStepRepository(Protocol):
    def add(self, step: RunStep) -> None: ...


class OutboxRepository(Protocol):
    def add(self, event: OutboxEvent) -> None: ...

    def get(self, event_id: UUID) -> OutboxEvent | None: ...

    def lock_unpublished(self, *, now: datetime, limit: int) -> list[OutboxEvent]: ...


class UnitOfWork(Protocol):
    runs: RunRepository
    steps: RunStepRepository
    outbox: OutboxRepository

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
