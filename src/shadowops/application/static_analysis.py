"""Static analysis stage orchestration and report queries."""

from collections.abc import Callable
from uuid import UUID

from shadowops.application.ports import UnitOfWork
from shadowops.domain.errors import (
    RepositoryInputError,
    RunNotFoundError,
    StaticReportNotReadyError,
)
from shadowops.domain.runs import AuditRun
from shadowops.rules.contracts import StaticReportV1
from shadowops.rules.service import StaticAuditService


class StaticAnalysisStageHandler:
    handler_version = "m2.static-analysis.v1"

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        audit_service: StaticAuditService,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service

    def execute(self, run: AuditRun, *, checkpoint: Callable[[], None]) -> None:
        with self._uow_factory() as uow:
            if uow.static_reports.get_for_run(run.id) is not None:
                return
            snapshot = uow.snapshots.get_for_run(run.id)
        if snapshot is None:
            raise RepositoryInputError(
                "SNAPSHOT_INTEGRITY_FAILED", "Static analysis snapshot was not found"
            )
        report = self._audit_service.analyze(run.id, snapshot.id)
        checkpoint()
        with self._uow_factory() as uow:
            uow.static_reports.create_or_get(report)
            uow.commit()


class StaticReportQueryService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def get(self, run_id: UUID) -> StaticReportV1:
        with self._uow_factory() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise RunNotFoundError(run_id)
            report = uow.static_reports.get_for_run(run_id)
        if report is None:
            raise StaticReportNotReadyError(run_id)
        return report
