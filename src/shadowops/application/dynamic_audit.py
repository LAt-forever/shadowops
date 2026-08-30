"""Deterministic M4 orchestration over fixed sandbox capabilities."""

from collections.abc import Callable

from shadowops.application.ports import UnitOfWork
from shadowops.domain.errors import RepositoryInputError
from shadowops.domain.runs import AuditRun
from shadowops.sandbox.contracts import RunnerAction, RunnerRequestV1, RunnerStatus
from shadowops.sandbox.docker_manager import DockerResourceManager


class ProvisionShadowStageHandler:
    handler_version = "m4.provision-shadow.v1"

    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], manager: DockerResourceManager
    ) -> None:
        self._uow_factory = uow_factory
        self._manager = manager

    def execute(self, run: AuditRun, *, checkpoint: Callable[[], None]) -> None:
        with self._uow_factory() as uow:
            snapshot = uow.snapshots.get_for_run(run.id)
            graph = uow.revision_graphs.get_for_run(run.id)
        if snapshot is None or graph is None or not graph.supported:
            raise RepositoryInputError(
                "UNSUPPORTED_REPOSITORY",
                "Dynamic audit requires a supported immutable revision graph",
            )
        if not graph.target_chain:
            raise RepositoryInputError(
                "UNSUPPORTED_REPOSITORY", "Dynamic audit requires baseline and target revisions"
            )
        try:
            self._manager.provision(run.id, 1, snapshot, checkpoint=checkpoint)
            checkpoint()
        except BaseException:
            self._manager.finalize_run(run.id)
            raise


class BaselineUpgradeStageHandler:
    handler_version = "m4.baseline-upgrade.v1"

    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], manager: DockerResourceManager
    ) -> None:
        self._uow_factory = uow_factory
        self._manager = manager

    def execute(self, run: AuditRun, *, checkpoint: Callable[[], None]) -> None:
        with self._uow_factory() as uow:
            graph = uow.revision_graphs.get_for_run(run.id)
        if graph is None or not graph.target_chain:
            raise RepositoryInputError("UNSUPPORTED_REPOSITORY", "Baseline revision is unavailable")
        try:
            execution = self._manager.execute(
                run.id,
                1,
                RunnerRequestV1(
                    action=RunnerAction.UPGRADE_BASELINE,
                    revision=graph.baseline_revision or "base",
                    statement_timeout_ms=30_000,
                    output_limit_bytes=65_536,
                ),
                checkpoint=checkpoint,
            )
            if execution.result.status is RunnerStatus.FAILED:
                raise RepositoryInputError(
                    execution.result.error_code or "MIGRATION_FAILED",
                    execution.result.error_detail or "Baseline migration failed",
                )
        except BaseException:
            self._manager.finalize_run(run.id)
            raise


class ApplyTargetStageHandler:
    handler_version = "m4.apply-target.v1"

    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], manager: DockerResourceManager
    ) -> None:
        self._uow_factory = uow_factory
        self._manager = manager

    def execute(self, run: AuditRun, *, checkpoint: Callable[[], None]) -> None:
        with self._uow_factory() as uow:
            graph = uow.revision_graphs.get_for_run(run.id)
        if graph is None or not graph.target_chain:
            raise RepositoryInputError("UNSUPPORTED_REPOSITORY", "Target revision is unavailable")
        pending_error: RepositoryInputError | None = None
        try:
            execution = self._manager.execute(
                run.id,
                1,
                RunnerRequestV1(
                    action=RunnerAction.APPLY_TARGET,
                    revision=graph.target_chain[-1],
                    statement_timeout_ms=30_000,
                    output_limit_bytes=65_536,
                ),
                checkpoint=checkpoint,
            )
            if execution.result.status is RunnerStatus.FAILED:
                pending_error = RepositoryInputError(
                    execution.result.error_code or "MIGRATION_FAILED",
                    execution.result.error_detail or "Target migration failed",
                )
        finally:
            self._manager.finalize_run(run.id)
        if pending_error is not None:
            raise pending_error
