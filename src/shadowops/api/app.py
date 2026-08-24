"""FastAPI application factory."""

from fastapi import FastAPI

from shadowops.api.routes.health import router as health_router
from shadowops.application.readiness import ReadinessService


def create_app(readiness_service: ReadinessService | None = None) -> FastAPI:
    application = FastAPI(title="ShadowOps", version="0.1.0")
    application.state.readiness_service = readiness_service or ReadinessService({})
    application.include_router(health_router)
    return application


app = create_app()
