"""Worker process runtime dependencies."""

from datetime import timedelta
from functools import lru_cache
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from shadowops.agent.gateway import ReadOnlyToolGateway
from shadowops.agent.provider import FakeAgentProvider
from shadowops.agent.runtime import AgentPlanner
from shadowops.application.discovery import DiscoveryStageHandler, NoOpStageHandler, StageHandler
from shadowops.application.dynamic_audit import (
    ApplyTargetStageHandler,
    BaselineUpgradeStageHandler,
    ProvisionShadowStageHandler,
)
from shadowops.application.planning import PlanningStageHandler
from shadowops.application.run_execution import RunExecutionService
from shadowops.application.static_analysis import StaticAnalysisStageHandler
from shadowops.config import get_settings
from shadowops.domain.runs import RunState
from shadowops.persistence.database import create_control_engine, create_session_factory
from shadowops.persistence.uow import SqlAlchemyUnitOfWork
from shadowops.repository.alembic import AlembicDiscoveryService
from shadowops.repository.contracts import RepoSnapshotV1, RevisionGraphV1
from shadowops.repository.snapshot import SnapshotReader, SnapshotService
from shadowops.rules.service import StaticAuditService
from shadowops.sandbox.docker_manager import DockerResourceManager
from shadowops.worker.outbox import CeleryEventPublisher, OutboxDispatcher
from shadowops.worker.reconciler import RunReconciler


@lru_cache
def get_worker_session_factory() -> sessionmaker[Session]:
    settings = get_settings()
    engine = create_control_engine(settings.database_url)
    return create_session_factory(engine)


@lru_cache
def get_execution_service() -> RunExecutionService:
    sessions = get_worker_session_factory()
    return RunExecutionService(lambda: SqlAlchemyUnitOfWork(sessions))


@lru_cache
def get_stage_handlers() -> dict[RunState, StageHandler]:
    settings = get_settings()
    sessions = get_worker_session_factory()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    def snapshot_lookup(snapshot_id: UUID) -> RepoSnapshotV1 | None:
        with uow_factory() as uow:
            return uow.snapshots.get(snapshot_id)

    def graph_lookup(run_id: UUID) -> RevisionGraphV1 | None:
        with uow_factory() as uow:
            return uow.revision_graphs.get_for_run(run_id)

    discovery = DiscoveryStageHandler(
        uow_factory,
        SnapshotService(
            settings.repo_root,
            settings.artifact_root,
            max_files=settings.snapshot_max_files,
            max_file_bytes=settings.snapshot_max_file_bytes,
            max_total_bytes=settings.snapshot_max_total_bytes,
            read_chunk_bytes=settings.snapshot_read_chunk_bytes,
        ),
        AlembicDiscoveryService(settings.artifact_root, snapshot_lookup),
    )
    static_analysis = StaticAnalysisStageHandler(
        uow_factory,
        StaticAuditService(
            snapshot_lookup,
            graph_lookup,
            SnapshotReader(settings.artifact_root, snapshot_lookup),
            max_source_bytes=settings.snapshot_max_file_bytes,
        ),
    )
    planning = PlanningStageHandler(
        uow_factory,
        AgentPlanner(
            FakeAgentProvider(),
            ReadOnlyToolGateway(
                uow_factory,
                SnapshotReader(settings.artifact_root, snapshot_lookup),
            ),
        ),
    )
    sandbox = get_sandbox_manager()
    provisioning = ProvisionShadowStageHandler(uow_factory, sandbox)
    baseline = BaselineUpgradeStageHandler(uow_factory, sandbox)
    applying = ApplyTargetStageHandler(uow_factory, sandbox)
    noop = NoOpStageHandler()
    return {
        state: (
            discovery
            if state is RunState.DISCOVERING
            else static_analysis
            if state is RunState.STATIC_ANALYSIS
            else planning
            if state is RunState.PLANNING
            else provisioning
            if state is RunState.PROVISIONING
            else baseline
            if state is RunState.BASELINE_READY
            else applying
            if state is RunState.APPLYING
            else noop
        )
        for state in RunState
    }


@lru_cache
def get_sandbox_manager() -> DockerResourceManager:
    settings = get_settings()
    sessions = get_worker_session_factory()
    return DockerResourceManager(
        lambda: SqlAlchemyUnitOfWork(sessions),
        settings.artifact_root,
        postgres_image=settings.shadow_postgres_image,
        runner_image=settings.runner_image,
        lease_duration=timedelta(seconds=settings.sandbox_lease_seconds),
        readiness_timeout_seconds=settings.sandbox_readiness_timeout_seconds,
        execution_timeout_seconds=settings.sandbox_execution_timeout_seconds,
    )


@lru_cache
def get_outbox_dispatcher() -> OutboxDispatcher:
    from shadowops.worker.celery_app import celery_app

    settings = get_settings()
    sessions = get_worker_session_factory()
    return OutboxDispatcher(
        lambda: SqlAlchemyUnitOfWork(sessions),
        CeleryEventPublisher(celery_app),
        retry_base=timedelta(seconds=settings.outbox_retry_base_seconds),
        retry_max=timedelta(seconds=settings.outbox_retry_max_seconds),
    )


@lru_cache
def get_run_reconciler() -> RunReconciler:
    settings = get_settings()
    sessions = get_worker_session_factory()
    return RunReconciler(
        lambda: SqlAlchemyUnitOfWork(sessions),
        stale_after=timedelta(seconds=settings.recovery_stale_after_seconds),
        max_attempts=settings.recovery_max_attempts,
    )
