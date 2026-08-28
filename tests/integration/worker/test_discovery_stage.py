import os
import subprocess
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shadowops.api.schemas.runs import CreateAuditRunRequestV1
from shadowops.application.discovery import DiscoveryStageHandler
from shadowops.application.run_execution import RunExecutionService
from shadowops.application.runs import RunService
from shadowops.domain.errors import RepositoryInputError
from shadowops.domain.runs import RunState
from shadowops.persistence.uow import SqlAlchemyUnitOfWork
from shadowops.repository.alembic import AlembicDiscoveryService
from shadowops.repository.contracts import RepoSnapshotV1
from shadowops.repository.snapshot import SnapshotService

TEST_DATABASE_URL = "postgresql+psycopg://shadowops:shadowops@127.0.0.1:55432/shadowops"


@pytest.fixture
def database() -> Engine:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE outbox_events, run_steps, audit_runs CASCADE"))
    yield engine
    engine.dispose()


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "ShadowOps Tests",
            "GIT_AUTHOR_EMAIL": "tests@shadowops.local",
            "GIT_COMMITTER_NAME": "ShadowOps Tests",
            "GIT_COMMITTER_EMAIL": "tests@shadowops.local",
            "GIT_AUTHOR_DATE": "2026-08-26T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-08-26T00:00:00Z",
        },
    )


def _fixture(root: Path) -> None:
    repository = root / "demo"
    versions = repository / "migrations" / "versions"
    versions.mkdir(parents=True)
    _git(repository, "init", "-q")
    (repository / "alembic.ini").write_text("[alembic]\nscript_location=migrations\n")
    (versions / "001.py").write_text(
        "revision='001'\ndown_revision=None\ndef upgrade(): pass\ndef downgrade(): pass\n"
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "fixture")


def _services(engine: Engine, root: Path, artifacts: Path):
    sessions = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    def factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    def lookup(snapshot_id: UUID) -> RepoSnapshotV1 | None:
        with factory() as uow:
            return uow.snapshots.get(snapshot_id)

    return (
        RunService(factory),
        RunExecutionService(factory, lease_duration=timedelta(seconds=10)),
        DiscoveryStageHandler(
            factory,
            SnapshotService(root, artifacts),
            AlembicDiscoveryService(artifacts, lookup),
        ),
    )


def _event_id(engine: Engine, run_id: UUID) -> UUID:
    with engine.connect() as connection:
        event_id = connection.execute(
            text(
                "SELECT id FROM outbox_events "
                "WHERE aggregate_id = :run_id AND aggregate_version = 1"
            ),
            {"run_id": run_id},
        ).scalar_one()
    assert isinstance(event_id, UUID)
    return event_id


def test_discovery_persists_one_snapshot_and_graph_across_replay(
    database: Engine, tmp_path: Path
) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    _fixture(root)
    runs, execution, handler = _services(database, root, tmp_path / "artifacts")
    run = runs.create(
        CreateAuditRunRequestV1(repository_path="demo"), idempotency_key="discovery-ok"
    )
    claim = execution.claim(_event_id(database, run.id), worker_id="worker-a")
    assert claim is not None
    current = execution.get_run_for_claim(claim)

    handler.execute(current, checkpoint=lambda: None)
    handler.execute(current, checkpoint=lambda: None)
    advanced = execution.finalize(claim)

    assert advanced.state is RunState.DISCOVERING
    with database.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM repo_snapshots")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM revision_graphs")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT supported FROM revision_graphs WHERE run_id = :run_id"),
                {"run_id": run.id},
            ).scalar_one()
            is True
        )


def test_security_failure_can_be_persisted_as_terminal_run_failure(
    database: Engine, tmp_path: Path
) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    runs, execution, handler = _services(database, root, tmp_path / "artifacts")
    run = runs.create(
        CreateAuditRunRequestV1(repository_path="missing"),
        idempotency_key="discovery-missing",
    )
    claim = execution.claim(_event_id(database, run.id), worker_id="worker-a")
    assert claim is not None

    with pytest.raises(RepositoryInputError) as error:
        handler.execute(execution.get_run_for_claim(claim), checkpoint=lambda: None)
    failed = execution.fail(claim, error_code=error.value.code, error_detail=str(error.value))

    assert failed.state is RunState.FAILED
    assert failed.failure_code == "REPOSITORY_NOT_FOUND"
    with database.connect() as connection:
        row = connection.execute(
            text("SELECT status, error_code FROM run_steps WHERE run_id = :run_id"),
            {"run_id": run.id},
        ).one()
    assert row == ("FAILED", "REPOSITORY_NOT_FOUND")
