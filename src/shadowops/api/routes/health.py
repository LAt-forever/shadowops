"""Service health routes."""

from fastapi import APIRouter, Request, Response, status

from shadowops.application.readiness import ReadinessService

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
def ready(request: Request, response: Response) -> dict[str, object]:
    service: ReadinessService = request.app.state.readiness_service
    result = service.run()
    if not result.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if result.ready else "not_ready",
        "dependencies": result.dependencies,
    }
