from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shadowops.api.app import create_app
from shadowops.api.schemas.runs import CreateAuditRunRequestV1
from shadowops.application.readiness import ReadinessService
from shadowops.application.run_execution import RunExecutionService
from shadowops.application.run_timeline import RunTimelineService
from shadowops.application.runs import RunService
from shadowops.domain.runs import MAIN_STATE_PATH, RunState
from shadowops.persistence.uow import SqlAlchemyUnitOfWork

TEST_DATABASE_URL = "postgresql+psycopg://shadowops:shadowops@127.0.0.1:55432/shadowops"


@pytest.fixture
def database() -> Engine:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE outbox_events, run_steps, audit_runs CASCADE"))
    yield engine
    engine.dispose()


def _event_id(engine: Engine, run_id: UUID, version: int) -> UUID:
    with engine.connect() as connection:
        value = connection.execute(
            text(
                "SELECT id FROM outbox_events "
                "WHERE aggregate_id = :run_id AND aggregate_version = :version"
            ),
            {"run_id": run_id, "version": version},
        ).scalar_one()
    assert isinstance(value, UUID)
    return value


def test_timeline_and_sse_resume_are_derived_from_persisted_versions(database: Engine) -> None:
    sessions = sessionmaker(bind=database, class_=Session, expire_on_commit=False)

    def factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    runs = RunService(factory)
    execution = RunExecutionService(factory, lease_duration=timedelta(seconds=10))
    timeline = RunTimelineService(factory)
    run = runs.create(
        CreateAuditRunRequestV1(repository_path="projects/demo"),
        idempotency_key="timeline-request",
    )
    while run.state is not RunState.COMPLETED:
        claim = execution.claim(_event_id(database, run.id, run.version), worker_id="worker-a")
        assert claim is not None
        run = execution.finalize(claim)

    client = TestClient(
        create_app(ReadinessService({}), run_service=runs, timeline_service=timeline)
    )
    response = client.get(f"/api/v1/runs/{run.id}/timeline")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_version"] == len(MAIN_STATE_PATH)
    assert payload["terminal"] is True
    assert [event["version"] for event in payload["events"]] == list(
        range(1, len(MAIN_STATE_PATH) + 1)
    )
    assert [event["state"] for event in payload["events"]] == [
        state.value for state in MAIN_STATE_PATH
    ]
    assert payload["current_step"] is None

    resumed = client.get(
        f"/api/v1/runs/{run.id}/events",
        headers={"Last-Event-ID": str(len(MAIN_STATE_PATH) - 2)},
    )

    assert resumed.status_code == 200
    assert f"id: {len(MAIN_STATE_PATH) - 2}\n" not in resumed.text
    assert f"id: {len(MAIN_STATE_PATH) - 1}\n" in resumed.text
    assert f"id: {len(MAIN_STATE_PATH)}\n" in resumed.text
