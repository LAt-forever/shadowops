"""Read-only query for persisted M4 environment and Runner evidence."""

from collections.abc import Callable
from uuid import UUID

from shadowops.application.ports import UnitOfWork
from shadowops.domain.errors import DynamicAuditNotReadyError, RunNotFoundError
from shadowops.sandbox.contracts import DynamicAuditViewV1


class DynamicAuditQueryService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def get(self, run_id: UUID, generation: int = 1) -> DynamicAuditViewV1:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFoundError(run_id)
            lease = uow.sandbox.get_environment(run_id, generation)
            if lease is None:
                raise DynamicAuditNotReadyError(run_id)
            executions = uow.sandbox.list_executions(lease.environment.id)
            evidence_items = uow.evidence.list_for_run(run_id)
        return DynamicAuditViewV1(
            run_id=run_id,
            generation=generation,
            environment=lease.environment,
            executions=tuple(executions),
            evidence_items=tuple(evidence_items),
        )
