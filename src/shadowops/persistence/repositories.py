"""SQLAlchemy repositories for audit runs, steps, and outbox events."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from shadowops.domain.errors import OptimisticConcurrencyError
from shadowops.domain.runs import (
    TERMINAL_STATES,
    AuditRun,
    CleanupStatus,
    OutboxEvent,
    RunState,
    RunStep,
    StepStatus,
)
from shadowops.persistence.models import AuditRunModel, OutboxEventModel, RunStepModel


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
