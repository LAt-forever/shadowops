from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shadowops.api.app import create_app
from shadowops.application.readiness import ReadinessService
from shadowops.application.run_execution import RunExecutionService
from shadowops.application.runs import RunService
from shadowops.domain.runs import RunState, StepStatus
from shadowops.persistence.uow import SqlAlchemyUnitOfWork

TEST_DATABASE_URL = "postgresql+psycopg://shadowops:shadowops@127.0.0.1:55432/shadowops"


@pytest.fixture
def database() -> Engine:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE outbox_events, run_steps, audit_runs CASCADE"))
    yield engine
    engine.dispose()


def test_cancelled_http_run_stops_at_the_worker_checkpoint(database: Engine) -> None:
    sessions = sessionmaker(bind=database, class_=Session, expire_on_commit=False)

    def factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    run_service = RunService(factory)
    client = TestClient(create_app(ReadinessService({}), run_service=run_service))
    created = client.post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "request-1"},
        json={"repository_path": "projects/demo"},
    ).json()
    run_id = UUID(str(created["id"]))

    requested = client.post(f"/api/v1/runs/{run_id}/cancel", json={"expected_version": 1})
    assert requested.status_code == 202
    assert requested.json()["cancel_requested_at"] is not None

    with database.connect() as connection:
        event_id = connection.execute(
            text("SELECT id FROM outbox_events WHERE aggregate_id = :run_id"),
            {"run_id": run_id},
        ).scalar_one()
    assert isinstance(event_id, UUID)
    execution = RunExecutionService(factory, lease_duration=timedelta(seconds=10))
    claim = execution.claim(event_id, worker_id="worker-a")
    assert claim is not None
    assert claim.to_state is RunState.CANCELLED
    cancelled = execution.finalize(claim)

    assert cancelled.state is RunState.CANCELLED
    replay = client.post(f"/api/v1/runs/{run_id}/cancel", json={"expected_version": 1})
    assert replay.status_code == 202
    assert replay.json()["state"] == "CANCELLED"
    with database.connect() as connection:
        step = connection.execute(
            text("SELECT status, to_state FROM run_steps WHERE run_id = :run_id"),
            {"run_id": run_id},
        ).one()
        outbox_count = connection.execute(
            text("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id = :run_id"),
            {"run_id": run_id},
        ).scalar_one()
    assert step == (StepStatus.CANCELLED.value, RunState.CANCELLED.value)
    assert outbox_count == 1
