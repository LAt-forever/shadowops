from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

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


def test_default_readiness_checks_database_and_redis(monkeypatch) -> None:
    class RedisClient:
        def ping(self) -> bool:
            return True

    monkeypatch.setattr(
        api_app,
        "create_engine",
        lambda *args, **kwargs: create_engine("sqlite+pysqlite:///:memory:"),
        raising=False,
    )
    monkeypatch.setattr(
        api_app,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: RedisClient()),
        raising=False,
    )

    response = TestClient(api_app.create_app()).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"database": "ok", "redis": "ok"},
    }
