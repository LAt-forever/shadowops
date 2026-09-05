"""M6 risk-report stage and read-only query service."""

from collections.abc import Callable
from uuid import UUID

from shadowops.application.ports import UnitOfWork
from shadowops.domain.errors import RepositoryInputError, RiskReportNotReadyError, RunNotFoundError
from shadowops.domain.runs import AuditRun
from shadowops.reporting.contracts import RiskReportV1
from shadowops.reporting.runtime import RiskReporter


class RiskReportingStageHandler:
    handler_version = "m6.risk-reporting.v1"

    def __init__(self, uow_factory: Callable[[], UnitOfWork], reporter: RiskReporter) -> None:
        self._uow_factory = uow_factory
        self._reporter = reporter

    def execute(self, run: AuditRun, *, checkpoint: Callable[[], None]) -> None:
        with self._uow_factory() as uow:
            if uow.risk_reports.get_for_run(run.id) is not None:
                return
        try:
            result = self._reporter.report(run.id)
        except Exception as exc:
            raise RepositoryInputError(
                "REPORT_GENERATION_FAILED", "Risk report inputs could not be validated"
            ) from exc
        checkpoint()
        with self._uow_factory() as uow:
            uow.risk_reports.save_result(result)
            uow.commit()


class RiskReportQueryService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def get(self, run_id: UUID) -> RiskReportV1:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFoundError(run_id)
            report = uow.risk_reports.get_for_run(run_id)
        if report is None:
            raise RiskReportNotReadyError(run_id)
        return report
