"""Worker process runtime dependencies."""

import json
from datetime import timedelta
from functools import lru_cache
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from shadowops.agent.gateway import ReadOnlyToolGateway
from shadowops.agent.llm import (
    FakeLLMProvider,
    LLMProvider,
    OpenAIResponsesProvider,
    PlannerLLMAdapter,
    RecordedLLMProvider,
)
from shadowops.agent.provider import AgentProvider, FakeAgentProvider
from shadowops.agent.runtime import AgentPlanner
from shadowops.application.discovery import DiscoveryStageHandler, NoOpStageHandler, StageHandler
from shadowops.application.dynamic_audit import (
    ApplyTargetStageHandler,
    BaselineUpgradeStageHandler,
    CollectEvidenceStageHandler,
    ProvisionShadowStageHandler,
    RollbackRoundtripStageHandler,
    SeedDataStageHandler,
    SmokeChecksStageHandler,
)
from shadowops.application.planning import PlanningStageHandler
from shadowops.application.reporting import RiskReportingStageHandler
from shadowops.application.run_execution import RunExecutionService
from shadowops.application.static_analysis import StaticAnalysisStageHandler
from shadowops.config import get_settings
from shadowops.domain.runs import RunState
from shadowops.evidence.collector import DynamicEvidenceCollector
from shadowops.evidence.store import LocalArtifactStore
from shadowops.persistence.database import create_control_engine, create_session_factory
from shadowops.persistence.uow import SqlAlchemyUnitOfWork
from shadowops.reporting.gateway import ReportingEvidenceGateway
from shadowops.reporting.runtime import RiskReporter
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
            get_planner_provider(),
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
    seeding = SeedDataStageHandler(uow_factory, sandbox)
    smoke = SmokeChecksStageHandler(uow_factory, sandbox)
    rollback = RollbackRoundtripStageHandler(uow_factory, sandbox)
    reporting = CollectEvidenceStageHandler(get_evidence_collector(), sandbox)
    risk_reporting = get_risk_reporting_handler()
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
            else seeding
            if state is RunState.SEEDING
            else smoke
            if state is RunState.SMOKE_TESTING
            else rollback
            if state is RunState.ROLLBACK_VERIFYING
            else reporting
            if state is RunState.REPORTING
            else risk_reporting
            if state is RunState.COMPLETED
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
def get_evidence_collector() -> DynamicEvidenceCollector:
    settings = get_settings()
    sessions = get_worker_session_factory()
    return DynamicEvidenceCollector(
        lambda: SqlAlchemyUnitOfWork(sessions), LocalArtifactStore(settings.artifact_root)
    )


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.agent_mode == "fake":
        return FakeLLMProvider()
    if settings.agent_mode == "recorded":
        if not settings.llm_model or not settings.llm_recorded_responses_json:
            raise RuntimeError("Recorded mode requires model and recorded responses JSON")
        payload = json.loads(settings.llm_recorded_responses_json)
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
        ):
            raise RuntimeError("Recorded responses must be a JSON string map")
        return RecordedLLMProvider(settings.llm_model, payload)
    if settings.openai_api_key is None or not settings.llm_model:
        raise RuntimeError("Live mode requires SHADOWOPS_OPENAI_API_KEY and SHADOWOPS_LLM_MODEL")
    return OpenAIResponsesProvider(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.llm_model,
        base_url=settings.openai_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
        max_attempts=settings.llm_max_attempts,
    )


@lru_cache
def get_planner_provider() -> AgentProvider:
    if get_settings().agent_mode == "fake":
        return FakeAgentProvider()
    return PlannerLLMAdapter(get_llm_provider())


@lru_cache
def get_risk_reporting_handler() -> RiskReportingStageHandler:
    settings = get_settings()
    sessions = get_worker_session_factory()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    store = LocalArtifactStore(settings.artifact_root)
    reporter = RiskReporter(get_llm_provider(), ReportingEvidenceGateway(uow_factory, store))
    return RiskReportingStageHandler(uow_factory, reporter)


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
