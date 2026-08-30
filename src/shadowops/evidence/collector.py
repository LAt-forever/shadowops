"""Collect persisted Runner results into immutable content-addressed evidence."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from shadowops.application.ports import UnitOfWork
from shadowops.evidence.contracts import EvidenceItemV1, EvidenceKind
from shadowops.evidence.store import LocalArtifactStore, StoredArtifact


class DynamicEvidenceCollector:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        store: LocalArtifactStore,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4

    def collect(self, run_id: UUID, generation: int = 1) -> tuple[EvidenceItemV1, ...]:
        with self._uow_factory() as uow:
            lease = uow.sandbox.get_environment(run_id, generation)
            if lease is None:
                raise RuntimeError("dynamic evidence requires a persisted shadow environment")
            executions = uow.sandbox.list_executions(lease.environment.id)
        candidates: list[EvidenceItemV1] = []
        for execution in executions:
            producer = f"runner.{execution.request.action.value.lower()}.v1"
            if execution.result.stdout.text:
                artifact = self._store.put_text(execution.result.stdout.text)
                candidates.append(
                    self._item(
                        run_id,
                        execution.id,
                        EvidenceKind.RUNNER_STDOUT,
                        producer,
                        artifact,
                        "text/plain",
                        "observed_in_shadow",
                        "REDACTED",
                    )
                )
            if execution.result.stderr.text:
                artifact = self._store.put_text(execution.result.stderr.text)
                candidates.append(
                    self._item(
                        run_id,
                        execution.id,
                        EvidenceKind.RUNNER_STDERR,
                        producer,
                        artifact,
                        "text/plain",
                        "observed_in_shadow",
                        "REDACTED",
                    )
                )
            for observation in execution.result.observations:
                artifact = self._store.put_json(observation.data)
                candidates.append(
                    self._item(
                        run_id,
                        execution.id,
                        EvidenceKind(observation.kind.value),
                        producer,
                        artifact,
                        "application/json",
                        "observed_in_shadow",
                        "NOT_REQUIRED",
                    )
                )
            if execution.result.coverage_gaps:
                artifact = self._store.put_json({"coverage_gaps": execution.result.coverage_gaps})
                candidates.append(
                    self._item(
                        run_id,
                        execution.id,
                        EvidenceKind.COVERAGE_GAPS,
                        producer,
                        artifact,
                        "application/json",
                        "unknown_in_production",
                        "NOT_REQUIRED",
                    )
                )
        with self._uow_factory() as uow:
            durable = tuple(uow.evidence.create_or_get(item) for item in candidates)
            uow.commit()
        return durable

    def _item(
        self,
        run_id: UUID,
        execution_id: UUID,
        kind: EvidenceKind,
        producer: str,
        artifact: StoredArtifact,
        media_type: str,
        scope: str,
        redaction: str,
    ) -> EvidenceItemV1:
        return EvidenceItemV1.model_validate(
            {
                "id": self._uuid_factory(),
                "run_id": run_id,
                "execution_id": execution_id,
                "kind": kind,
                "producer": producer,
                "observation_scope": scope,
                "artifact_uri": artifact.uri,
                "sha256": artifact.sha256,
                "byte_count": artifact.byte_count,
                "media_type": media_type,
                "redaction_status": redaction,
                "created_at": self._clock(),
            }
        )
