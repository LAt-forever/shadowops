from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from shadowops.api.app import create_app
from shadowops.application.readiness import ReadinessService
from shadowops.domain.errors import IdempotencyConflictError, RunNotFoundError
from shadowops.domain.runs import AuditRun, RunState

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 8, 25, 2, 0, tzinfo=UTC)


class StubRunService:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def create(self, *args: object, **kwargs: object) -> AuditRun:
        if self._error is not None:
            raise self._error
        return _run()

    def get(self, *args: object, **kwargs: object) -> AuditRun:
        if self._error is not None:
            raise self._error
        return _run()


def _run() -> AuditRun:
    return AuditRun(
        id=RUN_ID,
        state=RunState.QUEUED,
        version=1,
        repository_path="projects/demo",
        idempotency_key="request-1",
        request_fingerprint="a" * 64,
        created_at=NOW,
        updated_at=NOW,
    )


def _client(service: StubRunService) -> TestClient:
    return TestClient(create_app(ReadinessService({}), run_service=service))


def test_create_run_requires_idempotency_key_header() -> None:
    response = _client(StubRunService()).post(
        "/api/v1/runs", json={"repository_path": "projects/demo"}
    )

    assert response.status_code == 422


def test_create_run_returns_accepted_resource_and_location() -> None:
    response = _client(StubRunService()).post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "request-1"},
        json={"repository_path": "projects/demo"},
    )

    assert response.status_code == 202
    assert response.headers["location"] == f"/api/v1/runs/{RUN_ID}"
    assert response.json()["id"] == str(RUN_ID)
    assert response.json()["state"] == "QUEUED"
    assert response.json()["links"] == {
        "self": f"/api/v1/runs/{RUN_ID}",
        "events": f"/api/v1/runs/{RUN_ID}/events",
        "timeline": f"/api/v1/runs/{RUN_ID}/timeline",
    }


def test_create_run_maps_idempotency_conflict() -> None:
    response = _client(StubRunService(IdempotencyConflictError("request-1"))).post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "request-1"},
        json={"repository_path": "projects/demo"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_get_run_maps_not_found_to_stable_error() -> None:
    response = _client(StubRunService(RunNotFoundError(RUN_ID))).get(f"/api/v1/runs/{RUN_ID}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "RUN_NOT_FOUND"
