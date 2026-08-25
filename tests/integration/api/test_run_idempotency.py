from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shadowops.api.app import create_app
from shadowops.application.readiness import ReadinessService
from shadowops.application.runs import RunService
from shadowops.persistence.uow import SqlAlchemyUnitOfWork

TEST_DATABASE_URL = "postgresql+psycopg://shadowops:shadowops@127.0.0.1:55432/shadowops"


@pytest.fixture
def database() -> Engine:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE outbox_events, run_steps, audit_runs CASCADE"))
    yield engine
    engine.dispose()


def _client(engine: Engine) -> TestClient:
    sessions = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    service = RunService(lambda: SqlAlchemyUnitOfWork(sessions))
    return TestClient(create_app(ReadinessService({}), run_service=service))


def _create(client: TestClient, path: str = "projects/demo") -> tuple[int, dict[str, object]]:
    response = client.post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "concurrent-request"},
        json={"repository_path": path},
    )
    return response.status_code, response.json()


def test_http_replay_returns_one_run_and_one_outbox_event(database: Engine) -> None:
    client = _client(database)

    first_status, first = _create(client)
    replay_status, replay = _create(client)

    assert first_status == 202
    assert replay_status == 202
    assert replay["id"] == first["id"]
    with database.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM audit_runs")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM outbox_events")).scalar_one() == 1


def test_http_replay_rejects_a_different_payload(database: Engine) -> None:
    client = _client(database)
    assert _create(client, "projects/first")[0] == 202

    status_code, payload = _create(client, "projects/second")

    assert status_code == 409
    assert payload["detail"] == {"code": "IDEMPOTENCY_CONFLICT"}


def test_concurrent_http_replay_returns_the_same_run(database: Engine) -> None:
    client = _client(database)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: _create(client), range(2)))

    assert [status for status, _ in results] == [202, 202]
    assert len({str(payload["id"]) for _, payload in results}) == 1
    with database.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM audit_runs")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM outbox_events")).scalar_one() == 1
