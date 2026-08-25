"""Audit run command and query use cases."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from shadowops.api.schemas.runs import CreateAuditRunRequestV1
from shadowops.application.ports import UnitOfWork
from shadowops.domain.errors import IdempotencyConflictError, RunNotFoundError
from shadowops.domain.runs import AuditRun, OutboxEvent, RunState


def _request_fingerprint(request: CreateAuditRunRequestV1) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


class RunService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4

    def create(self, request: CreateAuditRunRequestV1, *, idempotency_key: str) -> AuditRun:
        fingerprint = _request_fingerprint(request)
        with self._uow_factory() as uow:
            existing = uow.runs.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return self._resolve_replay(existing, fingerprint, idempotency_key)

            now = self._clock()
            run = AuditRun(
                id=self._uuid_factory(),
                repository_path=request.repository_path,
                diff_mode=request.diff_mode.value,
                base_ref=request.base_ref,
                head_ref=request.head_ref,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                state=RunState.QUEUED,
                version=1,
                created_at=now,
                updated_at=now,
            )
            if not uow.runs.add_if_idempotency_absent(run):
                concurrent = uow.runs.get_by_idempotency_key(idempotency_key)
                if concurrent is None:
                    raise RuntimeError("Idempotency conflict did not expose the existing run")
                return self._resolve_replay(concurrent, fingerprint, idempotency_key)

            uow.outbox.add(
                OutboxEvent(
                    id=self._uuid_factory(),
                    aggregate_id=run.id,
                    aggregate_version=run.version,
                    topic="run.advance.requested.v1",
                    payload={
                        "run_id": str(run.id),
                        "expected_state": run.state.value,
                        "expected_version": run.version,
                    },
                    available_at=now,
                    created_at=now,
                )
            )
            uow.commit()
            return run

    def get(self, run_id: UUID) -> AuditRun:
        with self._uow_factory() as uow:
            run = uow.runs.get(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        return run

    @staticmethod
    def _resolve_replay(run: AuditRun, fingerprint: str, key: str) -> AuditRun:
        if run.request_fingerprint != fingerprint:
            raise IdempotencyConflictError(key)
        return run
