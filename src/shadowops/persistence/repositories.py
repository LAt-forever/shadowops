"""SQLAlchemy repositories for audit runs, steps, and outbox events."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from shadowops.domain.errors import OptimisticConcurrencyError
from shadowops.domain.runs import (
    AuditRun,
    CleanupStatus,
    OutboxEvent,
    RunState,
    RunStep,
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
