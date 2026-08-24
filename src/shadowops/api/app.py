"""FastAPI application factory."""

import redis
from fastapi import FastAPI
from sqlalchemy import create_engine

from shadowops.api.routes.health import router as health_router
from shadowops.application.readiness import ReadinessService
from shadowops.config import get_settings
from shadowops.infrastructure.health import DatabaseHealthCheck, RedisHealthCheck


def create_app(readiness_service: ReadinessService | None = None) -> FastAPI:
    if readiness_service is None:
        settings = get_settings()
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        redis_client = redis.from_url(settings.redis_url)
        readiness_service = ReadinessService(
            {
                "database": DatabaseHealthCheck(engine),
                "redis": RedisHealthCheck(redis_client),
            }
        )
    application = FastAPI(title="ShadowOps", version="0.1.0")
    application.state.readiness_service = readiness_service
    application.include_router(health_router)
    return application


app = create_app()
