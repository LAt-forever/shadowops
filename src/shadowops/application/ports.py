"""Application-facing persistence ports."""

from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from shadowops.agent.contracts import AuditPlanRecordV1, PlanningResultV1
from shadowops.domain.runs import AuditRun, OutboxEvent, RunState, RunStep
from shadowops.evidence.contracts import EvidenceItemV1
from shadowops.repository.contracts import RepoSnapshotV1, RevisionGraphV1
from shadowops.rules.contracts import StaticReportV1
from shadowops.sandbox.contracts import (
    RunnerAction,
    RunnerExecutionV1,
    ShadowEnvironmentLease,
    ShadowEnvironmentStatus,
    ShadowEnvironmentV1,
)


class RunRepository(Protocol):
    def add(self, run: AuditRun) -> None: ...

    def add_if_idempotency_absent(self, run: AuditRun) -> bool: ...

    def get(self, run_id: UUID) -> AuditRun | None: ...

    def get_by_idempotency_key(self, key: str) -> AuditRun | None: ...

    def save(self, run: AuditRun, *, expected_version: int) -> None: ...


class RunStepRepository(Protocol):
    def add(self, step: RunStep) -> None: ...

    def list_for_run(self, run_id: UUID) -> list[RunStep]: ...

    def get_current(self, run_id: UUID) -> RunStep | None: ...

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
        final_state: RunState,
    ) -> bool: ...

    def fail(
        self,
        step_id: UUID,
        *,
        claim_token: UUID,
        resulting_run_version: int,
        finished_at: datetime,
        error_code: str,
        error_detail: str,
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

    def wake_current(
        self, aggregate_id: UUID, *, aggregate_version: int, available_at: datetime
    ) -> bool: ...


class RepoSnapshotRepository(Protocol):
    def get(self, snapshot_id: UUID) -> RepoSnapshotV1 | None: ...

    def get_for_run(self, run_id: UUID) -> RepoSnapshotV1 | None: ...

    def create_or_get(self, snapshot: RepoSnapshotV1) -> RepoSnapshotV1: ...


class RevisionGraphRepository(Protocol):
    def get_for_run(self, run_id: UUID) -> RevisionGraphV1 | None: ...

    def create_or_get(self, graph: RevisionGraphV1) -> RevisionGraphV1: ...


class StaticReportRepository(Protocol):
    def get_for_run(self, run_id: UUID) -> StaticReportV1 | None: ...

    def create_or_get(self, report: StaticReportV1) -> StaticReportV1: ...


class AgentPlanningRepository(Protocol):
    def get_plan_for_run(self, run_id: UUID) -> AuditPlanRecordV1 | None: ...

    def save_result(self, result: PlanningResultV1) -> AuditPlanRecordV1 | None: ...


class SandboxRepository(Protocol):
    def get_environment(self, run_id: UUID, generation: int) -> ShadowEnvironmentLease | None: ...

    def create_or_get_environment(
        self, environment: ShadowEnvironmentV1, *, database_password: str
    ) -> ShadowEnvironmentLease: ...

    def set_environment_status(
        self,
        environment_id: UUID,
        *,
        status: ShadowEnvironmentStatus,
        cleaned_at: datetime | None,
    ) -> bool: ...

    def get_execution(
        self, environment_id: UUID, action: RunnerAction
    ) -> RunnerExecutionV1 | None: ...

    def create_or_get_execution(self, execution: RunnerExecutionV1) -> RunnerExecutionV1: ...

    def list_executions(self, environment_id: UUID) -> list[RunnerExecutionV1]: ...


class EvidenceRepository(Protocol):
    def create_or_get(self, item: EvidenceItemV1) -> EvidenceItemV1: ...

    def list_for_run(self, run_id: UUID) -> list[EvidenceItemV1]: ...


class UnitOfWork(Protocol):
    @property
    def runs(self) -> RunRepository: ...

    @property
    def steps(self) -> RunStepRepository: ...

    @property
    def outbox(self) -> OutboxRepository: ...

    @property
    def snapshots(self) -> RepoSnapshotRepository: ...

    @property
    def revision_graphs(self) -> RevisionGraphRepository: ...

    @property
    def static_reports(self) -> StaticReportRepository: ...

    @property
    def agent_planning(self) -> AgentPlanningRepository: ...

    @property
    def sandbox(self) -> SandboxRepository: ...

    @property
    def evidence(self) -> EvidenceRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
