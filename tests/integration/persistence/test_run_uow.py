from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from shadowops.domain.errors import OptimisticConcurrencyError
from shadowops.domain.runs import AuditRun, OutboxEvent, RunState, RunStep, StepStatus
from shadowops.persistence.uow import SqlAlchemyUnitOfWork

TEST_DATABASE_URL = "postgresql+psycopg://shadowops:shadowops@127.0.0.1:55432/shadowops"


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE outbox_events, run_steps, audit_runs CASCADE"))
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _run(*, key: str = "request-1") -> AuditRun:
    now = datetime.now(UTC)
    return AuditRun(
        id=uuid4(),
        state=RunState.QUEUED,
        version=1,
        updated_at=now,
        created_at=now,
        repository_path="projects/demo",
        diff_mode="WORKING_TREE",
        idempotency_key=key,
        request_fingerprint="a" * 64,
    )


def _event(run: AuditRun) -> OutboxEvent:
    now = datetime.now(UTC)
    return OutboxEvent(
        id=uuid4(),
        aggregate_id=run.id,
        aggregate_version=run.version,
        topic="run.advance.requested.v1",
        payload={"run_id": str(run.id), "expected_version": run.version},
        available_at=now,
        created_at=now,
    )


def test_uow_atomically_commits_a_run_and_its_initial_outbox_event(
    session_factory: sessionmaker[Session],
) -> None:
    run = _run()
    event = _event(run)

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.runs.add(run)
        uow.outbox.add(event)
        uow.commit()

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        stored_run = uow.runs.get(run.id)
        stored_event = uow.outbox.get(event.id)

    assert stored_run == run
    assert stored_event == event


def test_uow_rolls_back_run_and_outbox_when_not_committed(
    session_factory: sessionmaker[Session],
) -> None:
    run = _run()
    event = _event(run)

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.runs.add(run)
        uow.outbox.add(event)

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.runs.get(run.id) is None
        assert uow.outbox.get(event.id) is None


def test_run_update_rejects_a_stale_optimistic_version(
    session_factory: sessionmaker[Session],
) -> None:
    run = _run()
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.runs.add(run)
        uow.commit()

    first = SqlAlchemyUnitOfWork(session_factory)
    second = SqlAlchemyUnitOfWork(session_factory)
    with first, second:
        first_run = first.runs.get(run.id)
        second_run = second.runs.get(run.id)
        assert first_run is not None
        assert second_run is not None
        first_run.transition(RunState.DISCOVERING, now=datetime.now(UTC))
        second_run.transition(RunState.DISCOVERING, now=datetime.now(UTC))

        first.runs.save(first_run, expected_version=1)
        first.commit()

        with pytest.raises(OptimisticConcurrencyError):
            second.runs.save(second_run, expected_version=1)


def test_database_rejects_duplicate_idempotency_and_step_keys(
    session_factory: sessionmaker[Session],
) -> None:
    first_run = _run(key="same-request")
    second_run = _run(key="same-request")
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.runs.add(first_run)
        uow.commit()

    with pytest.raises(IntegrityError), SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.runs.add(second_run)
        uow.commit()

    first_step = RunStep(
        id=uuid4(),
        run_id=first_run.id,
        step_key=f"{first_run.id}:DISCOVERING:1",
        from_state=RunState.QUEUED,
        to_state=RunState.DISCOVERING,
        generation=1,
        attempt=1,
        status=StepStatus.RUNNING,
        expected_run_version=1,
        handler_version="m1.noop.v1",
        started_at=datetime.now(UTC),
    )
    duplicate_step = RunStep(**{**first_step.__dict__, "id": uuid4()})
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.steps.add(first_step)
        uow.commit()

    with pytest.raises(IntegrityError), SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.steps.add(duplicate_step)
        uow.commit()


def test_pending_outbox_claims_skip_rows_locked_by_another_dispatcher(
    session_factory: sessionmaker[Session],
) -> None:
    run = _run()
    event = _event(run)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.runs.add(run)
        uow.outbox.add(event)
        uow.commit()

    first = SqlAlchemyUnitOfWork(session_factory)
    second = SqlAlchemyUnitOfWork(session_factory)
    with first, second:
        first_claim = first.outbox.lock_unpublished(now=datetime.now(UTC), limit=1)
        second_claim = second.outbox.lock_unpublished(
            now=datetime.now(UTC) + timedelta(seconds=1), limit=1
        )

        assert [item.id for item in first_claim] == [event.id]
        assert second_claim == []
