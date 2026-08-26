"""Application orchestration for the real M2 discovery stage."""

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from shadowops.application.ports import UnitOfWork
from shadowops.domain.runs import AuditRun
from shadowops.repository.alembic import AlembicDiscoveryService
from shadowops.repository.contracts import RepoSnapshotV1, SnapshotRequestV1
from shadowops.repository.snapshot import SnapshotService


class StageHandler(Protocol):
    handler_version: str

    def execute(self, run: AuditRun, *, checkpoint: Callable[[], None]) -> None: ...


class NoOpStageHandler:
    handler_version = "m1.noop.v1"

    def execute(self, run: AuditRun, *, checkpoint: Callable[[], None]) -> None:
        checkpoint()


class DiscoveryStageHandler:
    handler_version = "m2.discovery.v1"

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        snapshot_service: SnapshotService,
        discovery_service: AlembicDiscoveryService,
    ) -> None:
        self._uow_factory = uow_factory
        self._snapshot_service = snapshot_service
        self._discovery_service = discovery_service

    def execute(self, run: AuditRun, *, checkpoint: Callable[[], None]) -> None:
        snapshot = self._existing_snapshot(run.id)
        if snapshot is None:
            request = SnapshotRequestV1.model_validate(
                {
                    "run_id": run.id,
                    "repository_path": run.repository_path,
                    "diff_mode": run.diff_mode,
                    "base_ref": run.base_ref,
                    "head_ref": run.head_ref,
                }
            )
            candidate = self._snapshot_service.create(
                request,
                checkpoint=checkpoint,
            )
            with self._uow_factory() as uow:
                snapshot = uow.snapshots.create_or_get(candidate)
                uow.commit()
        checkpoint()
        with self._uow_factory() as uow:
            existing_graph = uow.revision_graphs.get_for_run(run.id)
        if existing_graph is not None:
            return
        graph = self._discovery_service.discover(snapshot.id)
        checkpoint()
        with self._uow_factory() as uow:
            uow.revision_graphs.create_or_get(graph)
            uow.commit()

    def _existing_snapshot(self, run_id: UUID) -> RepoSnapshotV1 | None:
        with self._uow_factory() as uow:
            return uow.snapshots.get_for_run(run_id)
