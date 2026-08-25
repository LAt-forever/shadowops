from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shadowops.api.schemas.runs import CreateAuditRunRequestV1
from shadowops.application.run_execution import RunExecutionService
from shadowops.application.runs import RunService
from shadowops.domain.runs import OutboxEvent, RunState
from shadowops.persistence.uow import SqlAlchemyUnitOfWork
from shadowops.worker.outbox import OutboxDispatcher
from shadowops.worker.reconciler import ReconcileResult, RunReconciler

TEST_DATABASE_URL = "postgresql+psycopg://shadowops:shadowops@127.0.0.1:55432/shadowops"
STARTED_AT = datetime(2026, 8, 25, 5, 0, tzinfo=UTC)


@pytest.fixture
def database() -> Engine:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE outbox_events, run_steps, audit_runs CASCADE"))
    yield engine
    engine.dispose()


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[UUID] = []

    def publish(self, event: OutboxEvent) -> None:
        self.events.append(event.id)


def _factory(engine: Engine):
    sessions = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(sessions)


def _queued_run(engine: Engine):
    return RunService(_factory(engine), clock=lambda: STARTED_AT).create(
        CreateAuditRunRequestV1(repository_path="projects/demo"),
        idempotency_key="request-1",
    )


def _publish(engine: Engine) -> UUID:
    publisher = RecordingPublisher()
    dispatcher = OutboxDispatcher(_factory(engine), publisher, clock=lambda: STARTED_AT)
    assert dispatcher.dispatch_batch(limit=10) == 1
    return publisher.events[0]


def test_reconciler_reopens_a_published_event_with_no_live_claim(database: Engine) -> None:
    _queued_run(database)
    _publish(database)
    reconciler = RunReconciler(
        _factory(database),
        clock=lambda: STARTED_AT + timedelta(seconds=11),
        stale_after=timedelta(seconds=10),
    )

    result = reconciler.reconcile_batch(limit=10)

    assert result == ReconcileResult(reopened=1, failed=0)
    with database.connect() as connection:
        published_at = connection.execute(
            text("SELECT published_at FROM outbox_events")
        ).scalar_one()
    assert published_at is None


def test_reconciler_does_not_reopen_an_event_with_a_live_step_lease(database: Engine) -> None:
    run = _queued_run(database)
    event_id = _publish(database)
    execution = RunExecutionService(
        _factory(database),
        clock=lambda: STARTED_AT,
        lease_duration=timedelta(seconds=30),
    )
    assert execution.claim(event_id, worker_id="worker-a") is not None
    reconciler = RunReconciler(
        _factory(database),
        clock=lambda: STARTED_AT + timedelta(seconds=11),
        stale_after=timedelta(seconds=10),
    )

    result = reconciler.reconcile_batch(limit=10)

    assert result == ReconcileResult(reopened=0, failed=0)
    assert RunService(_factory(database)).get(run.id).state is RunState.QUEUED


def test_expired_step_is_republished_reclaimed_and_finalized(database: Engine) -> None:
    run = _queued_run(database)
    event_id = _publish(database)
    first_execution = RunExecutionService(
        _factory(database),
        clock=lambda: STARTED_AT,
        lease_duration=timedelta(seconds=10),
    )
    first = first_execution.claim(event_id, worker_id="worker-a")
    assert first is not None
    recovered_at = STARTED_AT + timedelta(seconds=11)
    reconciler = RunReconciler(
        _factory(database),
        clock=lambda: recovered_at,
        stale_after=timedelta(seconds=10),
    )
    assert reconciler.reconcile_batch(limit=10) == ReconcileResult(reopened=1, failed=0)
    publisher = RecordingPublisher()
    assert (
        OutboxDispatcher(_factory(database), publisher, clock=lambda: recovered_at).dispatch_batch(
            limit=10
        )
        == 1
    )
    recovered_execution = RunExecutionService(
        _factory(database),
        clock=lambda: recovered_at,
        lease_duration=timedelta(seconds=10),
    )

    recovered = recovered_execution.claim(event_id, worker_id="worker-b")
    assert recovered is not None
    assert recovered.id == first.id
    assert recovered.attempt == 2
    assert recovered.claim_token != first.claim_token
    advanced = recovered_execution.finalize(recovered)

    assert advanced.id == run.id
    assert advanced.state is RunState.DISCOVERING
