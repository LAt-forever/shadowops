from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shadowops.api.schemas.runs import CreateAuditRunRequestV1
from shadowops.application.run_execution import RunExecutionService
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


def _services(engine: Engine) -> tuple[RunService, RunExecutionService]:
    sessions = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    def factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    return RunService(factory), RunExecutionService(factory, lease_duration=timedelta(seconds=10))


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


def test_duplicate_consumers_create_one_logical_step(database: Engine) -> None:
    runs, execution = _services(database)
    run = runs.create(
        CreateAuditRunRequestV1(repository_path="projects/demo"),
        idempotency_key="request-1",
    )
    event_id = _event_id(database, run.id, run.version)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(
            executor.map(
                lambda worker: execution.claim(event_id, worker_id=f"worker-{worker}"),
                range(2),
            )
        )

    assert sum(claim is not None for claim in claims) == 1
    claim = next(item for item in claims if item is not None)
    execution.finalize(claim)
    assert execution.claim(event_id, worker_id="worker-c") is None
    with database.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM run_steps")).scalar_one() == 1


def test_walking_skeleton_persists_every_legal_transition(database: Engine) -> None:
    runs, execution = _services(database)
    run = runs.create(
        CreateAuditRunRequestV1(repository_path="projects/demo"),
        idempotency_key="request-1",
    )
    observed = [run.state]

    while run.state is not RunState.COMPLETED:
        event_id = _event_id(database, run.id, run.version)
        claim = execution.claim(event_id, worker_id="worker-a")
        assert claim is not None
        run = execution.finalize(claim)
        observed.append(run.state)

    assert observed == list(MAIN_STATE_PATH)
    with database.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM run_steps")).scalar_one() == 11
        assert connection.execute(text("SELECT COUNT(*) FROM outbox_events")).scalar_one() == 11
