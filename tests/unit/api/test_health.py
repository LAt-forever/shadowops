from fastapi.testclient import TestClient

import shadowops.api.app as api_app
from shadowops.application.readiness import ReadinessService


def test_liveness_does_not_depend_on_external_services() -> None:
    create_app = getattr(api_app, "create_app", None)
    assert create_app is not None
    client = TestClient(create_app(ReadinessService({})))

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_returns_503_with_dependency_status() -> None:
    def fail() -> None:
        raise ConnectionError("offline")

    create_app = getattr(api_app, "create_app", None)
    assert create_app is not None
    client = TestClient(create_app(ReadinessService({"database": fail})))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"database": "unavailable"},
    }
