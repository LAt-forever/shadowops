"""SQLAlchemy repositories for audit runs, steps, and outbox events."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from shadowops.agent.contracts import (
    AuditPlanRecordV1,
    PlanningResultV1,
)
from shadowops.domain.errors import ImmutableResultConflict, OptimisticConcurrencyError
from shadowops.domain.runs import (
    TERMINAL_STATES,
    AuditRun,
    CleanupStatus,
    OutboxEvent,
    RunState,
    RunStep,
    StepStatus,
)
from shadowops.persistence.models import (
    AgentInvocationModel,
    AgentToolCallModel,
    AuditPlanModel,
    AuditRunModel,
    OutboxEventModel,
    RepoSnapshotModel,
    RevisionGraphModel,
    RunnerExecutionModel,
    RunStepModel,
    ShadowEnvironmentModel,
    StaticReportModel,
)
from shadowops.repository.contracts import (
    GitChangeV1,
    RepoSnapshotV1,
    RevisionGraphV1,
    RevisionNodeV1,
    UnsupportedReasonV1,
)
from shadowops.rules.contracts import StaticReportV1
from shadowops.sandbox.contracts import (
    RunnerAction,
    RunnerExecutionV1,
    ShadowEnvironmentLease,
    ShadowEnvironmentStatus,
    ShadowEnvironmentV1,
)


def _to_run(model: AuditRunModel) -> AuditRun:
    return AuditRun(
        id=model.id,
        repository_path=model.repository_path,
        diff_mode=model.diff_mode,
        base_ref=model.base_ref,
        head_ref=model.head_ref,
        idempotency_key=model.idempotency_key,
        request_fingerprint=model.request_fingerprint,
        state=RunState(model.state),
        version=model.version,
        cleanup_status=CleanupStatus(model.cleanup_status),
        heartbeat_at=model.heartbeat_at,
        cancel_requested_at=model.cancel_requested_at,
        failure_code=model.failure_code,
        failure_detail=model.failure_detail,
        created_at=model.created_at,
        updated_at=model.updated_at,
        completed_at=model.completed_at,
    )


def _to_event(model: OutboxEventModel) -> OutboxEvent:
    return OutboxEvent(
        id=model.id,
        aggregate_id=model.aggregate_id,
        aggregate_version=model.aggregate_version,
        topic=model.topic,
        payload=dict(model.payload),
        available_at=model.available_at,
        published_at=model.published_at,
        publish_attempts=model.publish_attempts,
        last_error=model.last_error,
        created_at=model.created_at,
    )


def _to_step(model: RunStepModel) -> RunStep:
    return RunStep(
        id=model.id,
        run_id=model.run_id,
        step_key=model.step_key,
        from_state=RunState(model.from_state),
        to_state=RunState(model.to_state),
        generation=model.generation,
        attempt=model.attempt,
        status=StepStatus(model.status),
        expected_run_version=model.expected_run_version,
        resulting_run_version=model.resulting_run_version,
        handler_version=model.handler_version,
        worker_id=model.worker_id,
        claim_token=model.claim_token,
        heartbeat_at=model.heartbeat_at,
        lease_expires_at=model.lease_expires_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        error_code=model.error_code,
        error_detail=model.error_detail,
    )


class SqlAlchemyRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: AuditRun) -> None:
        self._session.add(
            AuditRunModel(
                id=run.id,
                repository_path=run.repository_path,
                diff_mode=run.diff_mode,
                base_ref=run.base_ref,
                head_ref=run.head_ref,
                idempotency_key=run.idempotency_key,
                request_fingerprint=run.request_fingerprint,
                state=run.state.value,
                version=run.version,
                cleanup_status=run.cleanup_status.value,
                heartbeat_at=run.heartbeat_at,
                cancel_requested_at=run.cancel_requested_at,
                failure_code=run.failure_code,
                failure_detail=run.failure_detail,
                created_at=run.created_at or run.updated_at,
                updated_at=run.updated_at,
                completed_at=run.completed_at,
            )
        )

    def add_if_idempotency_absent(self, run: AuditRun) -> bool:
        inserted_id = self._session.scalar(
            insert(AuditRunModel)
            .values(
                id=run.id,
                repository_path=run.repository_path,
                diff_mode=run.diff_mode,
                base_ref=run.base_ref,
                head_ref=run.head_ref,
                idempotency_key=run.idempotency_key,
                request_fingerprint=run.request_fingerprint,
                state=run.state.value,
                version=run.version,
                cleanup_status=run.cleanup_status.value,
                heartbeat_at=run.heartbeat_at,
                cancel_requested_at=run.cancel_requested_at,
                failure_code=run.failure_code,
                failure_detail=run.failure_detail,
                created_at=run.created_at or run.updated_at,
                updated_at=run.updated_at,
                completed_at=run.completed_at,
            )
            .on_conflict_do_nothing(index_elements=[AuditRunModel.idempotency_key])
            .returning(AuditRunModel.id)
        )
        return inserted_id is not None

    def get(self, run_id: UUID) -> AuditRun | None:
        model = self._session.get(AuditRunModel, run_id)
        return None if model is None else _to_run(model)

    def get_by_idempotency_key(self, key: str) -> AuditRun | None:
        model = self._session.scalar(
            select(AuditRunModel).where(AuditRunModel.idempotency_key == key)
        )
        return None if model is None else _to_run(model)

    def save(self, run: AuditRun, *, expected_version: int) -> None:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(AuditRunModel)
                .where(AuditRunModel.id == run.id, AuditRunModel.version == expected_version)
                .values(
                    state=run.state.value,
                    version=run.version,
                    cleanup_status=run.cleanup_status.value,
                    heartbeat_at=run.heartbeat_at,
                    cancel_requested_at=run.cancel_requested_at,
                    failure_code=run.failure_code,
                    failure_detail=run.failure_detail,
                    updated_at=run.updated_at,
                    completed_at=run.completed_at,
                )
            ),
        )
        if result.rowcount != 1:
            raise OptimisticConcurrencyError(run.id, expected_version)


class SqlAlchemyRunStepRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, step: RunStep) -> None:
        self._session.add(
            RunStepModel(
                id=step.id,
                run_id=step.run_id,
                step_key=step.step_key,
                from_state=step.from_state.value,
                to_state=step.to_state.value,
                generation=step.generation,
                attempt=step.attempt,
                status=step.status.value,
                expected_run_version=step.expected_run_version,
                resulting_run_version=step.resulting_run_version,
                handler_version=step.handler_version,
                worker_id=step.worker_id,
                claim_token=step.claim_token,
                heartbeat_at=step.heartbeat_at,
                lease_expires_at=step.lease_expires_at,
                started_at=step.started_at,
                finished_at=step.finished_at,
                error_code=step.error_code,
                error_detail=step.error_detail,
            )
        )

    def list_for_run(self, run_id: UUID) -> list[RunStep]:
        models = self._session.scalars(
            select(RunStepModel)
            .where(RunStepModel.run_id == run_id)
            .order_by(
                RunStepModel.resulting_run_version.asc().nulls_last(),
                RunStepModel.started_at,
                RunStepModel.id,
            )
        ).all()
        return [_to_step(model) for model in models]

    def get_current(self, run_id: UUID) -> RunStep | None:
        model = self._session.scalar(
            select(RunStepModel)
            .where(
                RunStepModel.run_id == run_id,
                RunStepModel.status == StepStatus.RUNNING.value,
            )
            .order_by(RunStepModel.started_at.desc(), RunStepModel.id.desc())
            .limit(1)
        )
        return None if model is None else _to_step(model)

    def claim(self, candidate: RunStep) -> RunStep | None:
        model = self._session.scalar(
            insert(RunStepModel)
            .values(
                id=candidate.id,
                run_id=candidate.run_id,
                step_key=candidate.step_key,
                from_state=candidate.from_state.value,
                to_state=candidate.to_state.value,
                generation=candidate.generation,
                attempt=candidate.attempt,
                status=candidate.status.value,
                expected_run_version=candidate.expected_run_version,
                resulting_run_version=candidate.resulting_run_version,
                handler_version=candidate.handler_version,
                worker_id=candidate.worker_id,
                claim_token=candidate.claim_token,
                heartbeat_at=candidate.heartbeat_at,
                lease_expires_at=candidate.lease_expires_at,
                started_at=candidate.started_at,
                finished_at=candidate.finished_at,
                error_code=candidate.error_code,
                error_detail=candidate.error_detail,
            )
            .on_conflict_do_update(
                constraint="uq_run_steps_run_step_key",
                set_={
                    "attempt": RunStepModel.attempt + 1,
                    "status": StepStatus.RUNNING.value,
                    "worker_id": candidate.worker_id,
                    "claim_token": candidate.claim_token,
                    "heartbeat_at": candidate.heartbeat_at,
                    "lease_expires_at": candidate.lease_expires_at,
                },
                where=and_(
                    RunStepModel.status == StepStatus.RUNNING.value,
                    or_(
                        RunStepModel.lease_expires_at.is_(None),
                        RunStepModel.lease_expires_at <= candidate.started_at,
                    ),
                ),
            )
            .returning(RunStepModel)
        )
        return None if model is None else _to_step(model)

    def heartbeat(
        self,
        step_id: UUID,
        *,
        claim_token: UUID,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(RunStepModel)
                .where(
                    RunStepModel.id == step_id,
                    RunStepModel.claim_token == claim_token,
                    RunStepModel.status == StepStatus.RUNNING.value,
                )
                .values(heartbeat_at=heartbeat_at, lease_expires_at=lease_expires_at)
            ),
        )
        return result.rowcount == 1

    def complete(
        self,
        step_id: UUID,
        *,
        claim_token: UUID,
        resulting_run_version: int,
        finished_at: datetime,
        final_state: RunState,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(RunStepModel)
                .where(
                    RunStepModel.id == step_id,
                    RunStepModel.claim_token == claim_token,
                    RunStepModel.status == StepStatus.RUNNING.value,
                )
                .values(
                    status=(
                        StepStatus.CANCELLED.value
                        if final_state is RunState.CANCELLED
                        else StepStatus.SUCCEEDED.value
                    ),
                    to_state=final_state.value,
                    resulting_run_version=resulting_run_version,
                    finished_at=finished_at,
                    lease_expires_at=None,
                )
            ),
        )
        return result.rowcount == 1

    def fail(
        self,
        step_id: UUID,
        *,
        claim_token: UUID,
        resulting_run_version: int,
        finished_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(RunStepModel)
                .where(
                    RunStepModel.id == step_id,
                    RunStepModel.claim_token == claim_token,
                    RunStepModel.status == StepStatus.RUNNING.value,
                )
                .values(
                    status=StepStatus.FAILED.value,
                    to_state=RunState.FAILED.value,
                    resulting_run_version=resulting_run_version,
                    finished_at=finished_at,
                    lease_expires_at=None,
                    error_code=error_code,
                    error_detail=error_detail,
                )
            ),
        )
        return result.rowcount == 1


class SqlAlchemyOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: OutboxEvent) -> None:
        self._session.add(
            OutboxEventModel(
                id=event.id,
                aggregate_id=event.aggregate_id,
                aggregate_version=event.aggregate_version,
                topic=event.topic,
                payload=event.payload,
                available_at=event.available_at,
                published_at=event.published_at,
                publish_attempts=event.publish_attempts,
                last_error=event.last_error,
                created_at=event.created_at,
            )
        )

    def get(self, event_id: UUID) -> OutboxEvent | None:
        model = self._session.get(OutboxEventModel, event_id)
        return None if model is None else _to_event(model)

    def lock_unpublished(self, *, now: datetime, limit: int) -> list[OutboxEvent]:
        models = self._session.scalars(
            select(OutboxEventModel)
            .where(
                OutboxEventModel.published_at.is_(None),
                OutboxEventModel.available_at <= now,
            )
            .order_by(OutboxEventModel.created_at, OutboxEventModel.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        return [_to_event(model) for model in models]

    def mark_published(self, event_id: UUID, *, published_at: datetime) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(OutboxEventModel)
                .where(
                    OutboxEventModel.id == event_id,
                    OutboxEventModel.published_at.is_(None),
                )
                .values(
                    published_at=published_at,
                    publish_attempts=OutboxEventModel.publish_attempts + 1,
                    last_error=None,
                )
            ),
        )
        return result.rowcount == 1

    def mark_failed(self, event_id: UUID, *, error: str, available_at: datetime) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(OutboxEventModel)
                .where(
                    OutboxEventModel.id == event_id,
                    OutboxEventModel.published_at.is_(None),
                )
                .values(
                    available_at=available_at,
                    publish_attempts=OutboxEventModel.publish_attempts + 1,
                    last_error=error,
                )
            ),
        )
        return result.rowcount == 1

    def lock_stale_deliveries(
        self, *, now: datetime, stale_before: datetime, limit: int
    ) -> list[OutboxEvent]:
        active_step = exists(
            select(RunStepModel.id).where(
                RunStepModel.run_id == OutboxEventModel.aggregate_id,
                RunStepModel.expected_run_version == OutboxEventModel.aggregate_version,
                RunStepModel.status == StepStatus.RUNNING.value,
                RunStepModel.lease_expires_at > now,
            )
        )
        models = self._session.scalars(
            select(OutboxEventModel)
            .join(AuditRunModel, AuditRunModel.id == OutboxEventModel.aggregate_id)
            .where(
                OutboxEventModel.published_at.is_not(None),
                OutboxEventModel.published_at <= stale_before,
                AuditRunModel.version == OutboxEventModel.aggregate_version,
                AuditRunModel.state.not_in([state.value for state in TERMINAL_STATES]),
                ~active_step,
            )
            .order_by(OutboxEventModel.published_at, OutboxEventModel.id)
            .limit(limit)
            .with_for_update(of=OutboxEventModel, skip_locked=True)
        ).all()
        return [_to_event(model) for model in models]

    def reopen(self, event_id: UUID, *, available_at: datetime, reason: str) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(OutboxEventModel)
                .where(OutboxEventModel.id == event_id)
                .values(published_at=None, available_at=available_at, last_error=reason)
            ),
        )
        return result.rowcount == 1

    def wake_current(
        self, aggregate_id: UUID, *, aggregate_version: int, available_at: datetime
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(OutboxEventModel)
                .where(
                    OutboxEventModel.aggregate_id == aggregate_id,
                    OutboxEventModel.aggregate_version == aggregate_version,
                    OutboxEventModel.topic == "run.advance.requested.v1",
                )
                .values(
                    published_at=None,
                    available_at=available_at,
                    last_error="WOKEN_FOR_CANCELLATION",
                )
            ),
        )
        return result.rowcount == 1


def _to_snapshot(model: RepoSnapshotModel) -> RepoSnapshotV1:
    return RepoSnapshotV1.model_validate(
        {
            "id": model.id,
            "run_id": model.run_id,
            "schema_version": model.schema_version,
            "source_path_hash": model.source_path_hash,
            "diff_mode": model.diff_mode,
            "base_commit": model.base_commit,
            "head_commit": model.head_commit,
            "dirty_diff_hash": model.dirty_diff_hash,
            "content_hash": model.content_hash,
            "artifact_uri": model.artifact_uri,
            "file_count": model.file_count,
            "total_bytes": model.total_bytes,
            "changed_paths": tuple(
                GitChangeV1.model_validate(item) for item in model.changed_paths
            ),
            "created_at": model.created_at,
        }
    )


class SqlAlchemyRepoSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, snapshot_id: UUID) -> RepoSnapshotV1 | None:
        model = self._session.get(RepoSnapshotModel, snapshot_id)
        return None if model is None else _to_snapshot(model)

    def get_for_run(self, run_id: UUID) -> RepoSnapshotV1 | None:
        model = self._session.scalar(
            select(RepoSnapshotModel).where(RepoSnapshotModel.run_id == run_id)
        )
        return None if model is None else _to_snapshot(model)

    def create_or_get(self, snapshot: RepoSnapshotV1) -> RepoSnapshotV1:
        self._session.execute(
            insert(RepoSnapshotModel)
            .values(
                id=snapshot.id,
                run_id=snapshot.run_id,
                schema_version=snapshot.schema_version,
                source_path_hash=snapshot.source_path_hash,
                diff_mode=snapshot.diff_mode,
                base_commit=snapshot.base_commit,
                head_commit=snapshot.head_commit,
                dirty_diff_hash=snapshot.dirty_diff_hash,
                content_hash=snapshot.content_hash,
                artifact_uri=snapshot.artifact_uri,
                file_count=snapshot.file_count,
                total_bytes=snapshot.total_bytes,
                changed_paths=[item.model_dump(mode="json") for item in snapshot.changed_paths],
                created_at=snapshot.created_at,
            )
            .on_conflict_do_nothing(index_elements=[RepoSnapshotModel.run_id])
        )
        existing = self.get_for_run(snapshot.run_id)
        if existing is None:
            raise RuntimeError("Snapshot upsert did not expose a durable result")
        if existing.model_dump(exclude={"id", "created_at"}) != snapshot.model_dump(
            exclude={"id", "created_at"}
        ):
            raise ImmutableResultConflict("repository snapshot")
        return existing


def _to_graph(model: RevisionGraphModel, snapshot: RepoSnapshotV1) -> RevisionGraphV1:
    return RevisionGraphV1.model_validate(
        {
            "id": model.id,
            "run_id": model.run_id,
            "snapshot_id": model.snapshot_id,
            "schema_version": model.schema_version,
            "diff_mode": snapshot.diff_mode,
            "base_commit": snapshot.base_commit,
            "head_commit": snapshot.head_commit,
            "nodes": tuple(RevisionNodeV1.model_validate(item) for item in model.nodes),
            "heads": tuple(model.heads),
            "baseline_revision": model.baseline_revision,
            "target_chain": tuple(model.target_chain),
            "changed_revisions": tuple(model.changed_revisions),
            "supported": model.supported,
            "unsupported_reasons": tuple(
                UnsupportedReasonV1.model_validate(item) for item in model.unsupported_reasons
            ),
            "created_at": model.created_at,
        }
    )


class SqlAlchemyRevisionGraphRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_run(self, run_id: UUID) -> RevisionGraphV1 | None:
        model = self._session.scalar(
            select(RevisionGraphModel).where(RevisionGraphModel.run_id == run_id)
        )
        if model is None:
            return None
        snapshot_model = self._session.get(RepoSnapshotModel, model.snapshot_id)
        if snapshot_model is None:
            raise RuntimeError("Revision graph references a missing snapshot")
        return _to_graph(model, _to_snapshot(snapshot_model))

    def create_or_get(self, graph: RevisionGraphV1) -> RevisionGraphV1:
        self._session.execute(
            insert(RevisionGraphModel)
            .values(
                id=graph.id,
                run_id=graph.run_id,
                snapshot_id=graph.snapshot_id,
                schema_version=graph.schema_version,
                supported=graph.supported,
                nodes=[item.model_dump(mode="json") for item in graph.nodes],
                heads=list(graph.heads),
                baseline_revision=graph.baseline_revision,
                target_chain=list(graph.target_chain),
                changed_revisions=list(graph.changed_revisions),
                unsupported_reasons=[
                    item.model_dump(mode="json") for item in graph.unsupported_reasons
                ],
                created_at=graph.created_at,
            )
            .on_conflict_do_nothing(index_elements=[RevisionGraphModel.run_id])
        )
        existing = self.get_for_run(graph.run_id)
        if existing is None:
            raise RuntimeError("Revision graph upsert did not expose a durable result")
        if existing.model_dump(exclude={"id", "created_at"}) != graph.model_dump(
            exclude={"id", "created_at"}
        ):
            raise ImmutableResultConflict("revision graph")
        return existing


class SqlAlchemyStaticReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_run(self, run_id: UUID) -> StaticReportV1 | None:
        model = self._session.scalar(
            select(StaticReportModel).where(StaticReportModel.run_id == run_id)
        )
        return None if model is None else StaticReportV1.model_validate(model.report)

    def create_or_get(self, report: StaticReportV1) -> StaticReportV1:
        self._session.execute(
            insert(StaticReportModel)
            .values(
                id=report.id,
                run_id=report.run_id,
                snapshot_id=report.snapshot_id,
                schema_version=report.schema_version,
                ruleset_version=report.ruleset_version,
                risk_level=report.risk_level,
                report=report.model_dump(mode="json"),
                created_at=report.created_at,
            )
            .on_conflict_do_nothing(index_elements=[StaticReportModel.run_id])
        )
        existing = self.get_for_run(report.run_id)
        if existing is None:
            raise RuntimeError("Static report upsert did not expose a durable result")
        if existing.model_dump(exclude={"id", "created_at"}) != report.model_dump(
            exclude={"id", "created_at"}
        ):
            raise ImmutableResultConflict("static report")
        return existing


class SqlAlchemyAgentPlanningRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_plan_for_run(self, run_id: UUID) -> AuditPlanRecordV1 | None:
        model = self._session.scalar(select(AuditPlanModel).where(AuditPlanModel.run_id == run_id))
        if model is None:
            return None
        return AuditPlanRecordV1.model_validate(
            {
                "id": model.id,
                "run_id": model.run_id,
                "invocation_id": model.invocation_id,
                "input_hash": model.input_hash,
                "plan": model.plan,
                "created_at": model.created_at,
            }
        )

    def save_result(self, result: PlanningResultV1) -> AuditPlanRecordV1 | None:
        invocation = result.invocation
        self._session.execute(
            insert(AgentInvocationModel)
            .values(**invocation.model_dump(mode="python"))
            .on_conflict_do_nothing(constraint="uq_agent_run_phase")
        )
        for call in result.tool_calls:
            values = call.model_dump(mode="python", exclude={"observation"})
            values["observation"] = call.observation.model_dump(mode="json")
            self._session.execute(
                insert(AgentToolCallModel)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_agent_tool_call_sequence")
            )
        if result.plan is not None:
            record = result.plan
            self._session.execute(
                insert(AuditPlanModel)
                .values(
                    id=record.id,
                    run_id=record.run_id,
                    invocation_id=record.invocation_id,
                    input_hash=record.input_hash,
                    plan=record.plan.model_dump(mode="json"),
                    created_at=record.created_at,
                )
                .on_conflict_do_nothing(index_elements=[AuditPlanModel.run_id])
            )
        existing = self.get_plan_for_run(invocation.run_id)
        if result.plan is not None and existing is None:
            raise RuntimeError("Agent planning upsert did not expose a durable plan")
        if (
            result.plan is not None
            and existing is not None
            and existing.model_dump(exclude={"id", "created_at"})
            != result.plan.model_dump(exclude={"id", "created_at"})
        ):
            raise ImmutableResultConflict("audit plan")
        return existing


class SqlAlchemySandboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _lease(model: ShadowEnvironmentModel) -> ShadowEnvironmentLease:
        environment = ShadowEnvironmentV1.model_validate(
            {
                "id": model.id,
                "run_id": model.run_id,
                "generation": model.generation,
                "schema_version": model.schema_version,
                "status": model.status,
                "postgres_container_id": model.postgres_container_id,
                "network_id": model.network_id,
                "volume_name": model.volume_name,
                "snapshot_volume_name": model.snapshot_volume_name,
                "postgres_image": model.postgres_image,
                "postgres_image_id": model.postgres_image_id,
                "runner_image": model.runner_image,
                "runner_image_id": model.runner_image_id,
                "lease_expires_at": model.lease_expires_at,
                "created_at": model.created_at,
                "cleaned_at": model.cleaned_at,
            }
        )
        return ShadowEnvironmentLease(environment, model.database_password)

    def get_environment(self, run_id: UUID, generation: int) -> ShadowEnvironmentLease | None:
        model = self._session.scalar(
            select(ShadowEnvironmentModel).where(
                ShadowEnvironmentModel.run_id == run_id,
                ShadowEnvironmentModel.generation == generation,
            )
        )
        return None if model is None else self._lease(model)

    def create_or_get_environment(
        self, environment: ShadowEnvironmentV1, *, database_password: str
    ) -> ShadowEnvironmentLease:
        values = environment.model_dump(mode="python")
        values["status"] = environment.status.value
        values["database_password"] = database_password
        self._session.execute(
            insert(ShadowEnvironmentModel)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_shadow_run_generation")
        )
        existing = self.get_environment(environment.run_id, environment.generation)
        if existing is None:
            raise RuntimeError("Shadow environment upsert did not expose a durable result")
        if existing.environment.model_dump(exclude={"id", "created_at"}) != environment.model_dump(
            exclude={"id", "created_at"}
        ):
            raise ImmutableResultConflict("shadow environment")
        return existing

    def set_environment_status(
        self,
        environment_id: UUID,
        *,
        status: ShadowEnvironmentStatus,
        cleaned_at: datetime | None,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(ShadowEnvironmentModel)
                .where(ShadowEnvironmentModel.id == environment_id)
                .values(status=status.value, cleaned_at=cleaned_at)
            ),
        )
        return result.rowcount == 1

    def get_execution(self, environment_id: UUID, action: RunnerAction) -> RunnerExecutionV1 | None:
        model = self._session.scalar(
            select(RunnerExecutionModel).where(
                RunnerExecutionModel.environment_id == environment_id,
                RunnerExecutionModel.action == action.value,
            )
        )
        if model is None:
            return None
        return RunnerExecutionV1.model_validate(
            {
                "id": model.id,
                "environment_id": model.environment_id,
                "run_id": model.run_id,
                "generation": model.generation,
                "schema_version": model.schema_version,
                "request": model.request,
                "result": model.result,
                "created_at": model.created_at,
            }
        )

    def create_or_get_execution(self, execution: RunnerExecutionV1) -> RunnerExecutionV1:
        self._session.execute(
            insert(RunnerExecutionModel)
            .values(
                id=execution.id,
                environment_id=execution.environment_id,
                run_id=execution.run_id,
                generation=execution.generation,
                schema_version=execution.schema_version,
                action=execution.request.action.value,
                request=execution.request.model_dump(mode="json"),
                result=execution.result.model_dump(mode="json"),
                created_at=execution.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_runner_environment_action")
        )
        existing = self.get_execution(execution.environment_id, execution.request.action)
        if existing is None:
            raise RuntimeError("Runner execution upsert did not expose a durable result")
        if existing.model_dump(exclude={"id", "created_at"}) != execution.model_dump(
            exclude={"id", "created_at"}
        ):
            raise ImmutableResultConflict("runner execution")
        return existing

    def list_executions(self, environment_id: UUID) -> list[RunnerExecutionV1]:
        models = self._session.scalars(
            select(RunnerExecutionModel)
            .where(RunnerExecutionModel.environment_id == environment_id)
            .order_by(RunnerExecutionModel.created_at, RunnerExecutionModel.id)
        ).all()
        return [
            RunnerExecutionV1.model_validate(
                {
                    "id": model.id,
                    "environment_id": model.environment_id,
                    "run_id": model.run_id,
                    "generation": model.generation,
                    "schema_version": model.schema_version,
                    "request": model.request,
                    "result": model.result,
                    "created_at": model.created_at,
                }
            )
            for model in models
        ]
