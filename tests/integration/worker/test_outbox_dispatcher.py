from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shadowops.api.schemas.runs import CreateAuditRunRequestV1
from shadowops.application.runs import RunService
from shadowops.domain.runs import OutboxEvent
from shadowops.persistence.uow import SqlAlchemyUnitOfWork
from shadowops.worker.outbox import OutboxDispatcher

TEST_DATABASE_URL = "postgresql+psycopg://shadowops:shadowops@127.0.0.1:55432/shadowops"


@pytest.fixture
def database() -> Engine:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE outbox_events, run_steps, audit_runs CASCADE"))
    yield engine
    engine.dispose()


class SlowRecordingPublisher:
    def __init__(self) -> None:
        self._lock = Lock()
        self.event_ids: list[UUID] = []

    def publish(self, event: OutboxEvent) -> None:
        sleep(0.05)
        with self._lock:
            self.event_ids.append(event.id)


def _factory(engine: Engine):
    sessions = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(sessions)


def _create_run(engine: Engine) -> None:
    RunService(_factory(engine)).create(
        CreateAuditRunRequestV1(repository_path="projects/demo"),
        idempotency_key="request-1",
    )


def test_concurrent_dispatchers_publish_a_locked_event_once(database: Engine) -> None:
    _create_run(database)
    publisher = SlowRecordingPublisher()
    dispatcher = OutboxDispatcher(_factory(database), publisher)

    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = list(executor.map(lambda _: dispatcher.dispatch_batch(limit=10), range(2)))

    assert sum(counts) == 1
    assert len(publisher.event_ids) == 1
    with database.connect() as connection:
        published_at = connection.execute(
            text("SELECT published_at FROM outbox_events")
        ).scalar_one()
    assert published_at is not None
