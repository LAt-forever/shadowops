"""Worker process runtime dependencies."""

from datetime import timedelta
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from shadowops.application.run_execution import RunExecutionService
from shadowops.config import get_settings
from shadowops.persistence.database import create_control_engine, create_session_factory
from shadowops.persistence.uow import SqlAlchemyUnitOfWork
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
