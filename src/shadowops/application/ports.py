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

    def claim(self, candidate: RunStep) -> RunStep | None: ...

    def heartbeat(
        self,
        step_id: UUID,
        *,
        claim_token: UUID,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> bool: ...

    def complete(
        self,
        step_id: UUID,
        *,
        claim_token: UUID,
        resulting_run_version: int,
        finished_at: datetime,
    ) -> bool: ...


class OutboxRepository(Protocol):
    def add(self, event: OutboxEvent) -> None: ...

    def get(self, event_id: UUID) -> OutboxEvent | None: ...

    def lock_unpublished(self, *, now: datetime, limit: int) -> list[OutboxEvent]: ...

    def mark_published(self, event_id: UUID, *, published_at: datetime) -> bool: ...

    def mark_failed(self, event_id: UUID, *, error: str, available_at: datetime) -> bool: ...

    def lock_stale_deliveries(
        self, *, now: datetime, stale_before: datetime, limit: int
    ) -> list[OutboxEvent]: ...

    def reopen(self, event_id: UUID, *, available_at: datetime, reason: str) -> bool: ...


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
