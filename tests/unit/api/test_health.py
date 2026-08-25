import json
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


def test_api_factory_emits_structured_runtime_log(capsys) -> None:
    api_app.create_app(ReadinessService({}))

    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "service_configured"
    assert payload["service"] == "api"
    assert payload["version"] == "0.1.0"


def test_default_readiness_checks_database_and_redis(monkeypatch) -> None:
    database_options: dict[str, object] = {}
    redis_options: dict[str, object] = {}

    class RedisClient:
        def ping(self) -> bool:
            return True

    def engine_factory(*args, **kwargs):
        database_options.update(kwargs)
        return create_engine("sqlite+pysqlite:///:memory:")

    def redis_factory(*args, **kwargs) -> RedisClient:
        redis_options.update(kwargs)
        return RedisClient()

    monkeypatch.setattr(
        api_app,
        "create_engine",
        engine_factory,
        raising=False,
    )
    monkeypatch.setattr(
        api_app,
        "redis",
        SimpleNamespace(from_url=redis_factory),
        raising=False,
    )

    response = TestClient(api_app.create_app()).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"database": "ok", "redis": "ok"},
    }
    assert database_options == {
        "connect_args": {
            "connect_timeout": 2,
            "options": "-c statement_timeout=2000",
        },
        "pool_pre_ping": True,
        "pool_timeout": 2.0,
    }
    assert redis_options == {
        "socket_connect_timeout": 2,
        "socket_timeout": 2.0,
    }


def test_default_dependency_clients_close_on_shutdown(monkeypatch) -> None:
    closed = {"database": False, "redis": False}
    engine = create_engine("sqlite+pysqlite:///:memory:")

    def dispose() -> None:
        closed["database"] = True

    class RedisClient:
        def ping(self) -> bool:
            return True

        def close(self) -> None:
            closed["redis"] = True

    monkeypatch.setattr(engine, "dispose", dispose)
    monkeypatch.setattr(api_app, "create_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(
        api_app,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: RedisClient()),
    )

    with TestClient(api_app.create_app()):
        pass

    assert closed == {"database": True, "redis": True}
