"""FastAPI application factory."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import redis
import structlog
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shadowops import __version__
from shadowops.api.routes.health import router as health_router
from shadowops.api.routes.run_events import router as run_events_router
from shadowops.api.routes.runs import router as runs_router
from shadowops.application.readiness import ReadinessService
from shadowops.application.run_timeline import RunTimelineService
from shadowops.application.runs import RunService
from shadowops.application.static_analysis import StaticReportQueryService
from shadowops.config import get_settings
from shadowops.infrastructure.health import DatabaseHealthCheck, RedisHealthCheck
from shadowops.observability.logging import configure_logging
from shadowops.persistence.uow import SqlAlchemyUnitOfWork


def create_app(
    readiness_service: ReadinessService | None = None,
    *,
    run_service: RunService | None = None,
    timeline_service: RunTimelineService | None = None,
    static_report_service: StaticReportQueryService | None = None,
) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    close_dependencies: Callable[[], None] | None = None
    if readiness_service is None:
        engine = create_engine(
            settings.database_url,
            connect_args={
                "connect_timeout": settings.health_connect_timeout_seconds,
                "options": f"-c statement_timeout={settings.health_statement_timeout_ms}",
            },
            pool_pre_ping=True,
            pool_timeout=settings.health_pool_timeout_seconds,
        )
        redis_client = redis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.health_connect_timeout_seconds,
            socket_timeout=settings.health_read_timeout_seconds,
        )
        readiness_service = ReadinessService(
            {
                "database": DatabaseHealthCheck(engine),
                "redis": RedisHealthCheck(redis_client),
            }
        )
        if run_service is None:
            session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            run_service = RunService(lambda: SqlAlchemyUnitOfWork(session_factory))
        if timeline_service is None:
            session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            timeline_service = RunTimelineService(lambda: SqlAlchemyUnitOfWork(session_factory))
        if static_report_service is None:
            session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            static_report_service = StaticReportQueryService(
                lambda: SqlAlchemyUnitOfWork(session_factory)
            )

        def close_default_dependencies() -> None:
            try:
                redis_client.close()
            finally:
                engine.dispose()

        close_dependencies = close_default_dependencies

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if close_dependencies is not None:
                close_dependencies()

    application = FastAPI(title="ShadowOps", version=__version__, lifespan=lifespan)
    application.state.readiness_service = readiness_service
    application.state.run_service = run_service
    application.state.timeline_service = timeline_service
    application.state.static_report_service = static_report_service
    application.state.sse_poll_interval_seconds = settings.sse_poll_interval_seconds
    application.state.sse_keepalive_seconds = settings.sse_keepalive_seconds
    application.include_router(health_router)
    application.include_router(runs_router)
    application.include_router(run_events_router)
    structlog.get_logger(__name__).info(
        "service_configured",
        service="api",
        version=__version__,
    )
    return application


app = create_app()
