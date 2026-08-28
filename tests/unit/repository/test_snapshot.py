import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from shadowops.domain.errors import RepositoryInputError
from shadowops.repository.contracts import SnapshotRequestV1
from shadowops.repository.snapshot import SnapshotReader, SnapshotService

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        check=True,
        text=True,
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
    return result.stdout.strip()


def _repository(root: Path) -> Path:
    repository = root / "demo"
    repository.mkdir()
    _git(repository, "init", "-q")
    (repository / "alembic.ini").write_text("[alembic]\nscript_location = migrations\n")
    versions = repository / "migrations" / "versions"
    versions.mkdir(parents=True)
    (versions / "001.py").write_text(
        "revision = '001'\ndown_revision = None\nbranch_labels = None\n"
        "depends_on = None\ndef upgrade(): pass\ndef downgrade(): pass\n"
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "initial")
    return repository


def _service(root: Path, artifacts: Path, **budgets: int) -> SnapshotService:
    return SnapshotService(
        root,
        artifacts,
        clock=lambda: NOW,
        uuid_factory=lambda: UUID("22222222-2222-4222-8222-222222222222"),
        **budgets,
    )


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/demo",
        "../demo",
        "demo/../outside",
        "demo//nested",
        "demo/./nested",
        "demo\x00x",
    ],
)
def test_rejects_paths_outside_allowed_root(tmp_path: Path, path: str) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    service = _service(root, tmp_path / "artifacts")

    with pytest.raises(RepositoryInputError) as error:
        service.create(SnapshotRequestV1(run_id=RUN_ID, repository_path=path))

    assert error.value.code == "REPOSITORY_OUTSIDE_ALLOWED_ROOT"


def test_working_tree_snapshot_is_deterministic_and_excludes_credentials(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    repository = _repository(root)
    (repository / "migrations" / "versions" / "001.py").write_text(
        "revision = '001'\ndown_revision = None\n# modified\n"
    )
    (repository / "notes.txt").write_text("untracked")
    (repository / ".env").write_text("SECRET=do-not-copy")
    (repository / ".env.example").write_text("SECRET=example")
    service = _service(root, tmp_path / "artifacts")
    request = SnapshotRequestV1(run_id=RUN_ID, repository_path="demo")

    first = service.create(request)
    second = service.create(request)

    assert first.content_hash == second.content_hash
    assert first.dirty_diff_hash == second.dirty_diff_hash
    tree = tmp_path / "artifacts" / "snapshots" / first.content_hash / "tree"
    assert (tree / "notes.txt").read_text() == "untracked"
    assert not (tree / ".env").exists()
    assert (tree / ".env.example").exists()
    assert first.artifact_uri == f"artifact://snapshots/{first.content_hash}"


def test_range_snapshot_uses_head_commit_and_ignores_live_changes(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    repository = _repository(root)
    base = _git(repository, "rev-parse", "HEAD")
    revision = repository / "migrations" / "versions" / "002.py"
    revision.write_text("revision = '002'\ndown_revision = '001'\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "second")
    head = _git(repository, "rev-parse", "HEAD")
    revision.write_text("live change must not be captured")
    service = _service(root, tmp_path / "artifacts")

    snapshot = service.create(
        SnapshotRequestV1(
            run_id=RUN_ID,
            repository_path="demo",
            diff_mode="RANGE",
            base_ref=base,
            head_ref=head,
        )
    )

    copied = (
        tmp_path
        / "artifacts"
        / "snapshots"
        / snapshot.content_hash
        / "tree"
        / "migrations"
        / "versions"
        / "002.py"
    )
    assert "down_revision = '001'" in copied.read_text()
    assert "live change" not in copied.read_text()
    assert snapshot.base_commit == base
    assert snapshot.head_commit == head


def test_rejects_symlinks_and_budget_exhaustion(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    repository = _repository(root)
    (repository / "linked.txt").symlink_to(repository / "alembic.ini")
    _git(repository, "add", "linked.txt")
    service = _service(root, tmp_path / "artifacts")

    with pytest.raises(RepositoryInputError) as symlink_error:
        service.create(SnapshotRequestV1(run_id=RUN_ID, repository_path="demo"))
    assert symlink_error.value.code == "REPOSITORY_SYMLINK_REJECTED"

    (repository / "linked.txt").unlink()
    _git(repository, "reset", "-q")
    limited = _service(root, tmp_path / "limited", max_total_bytes=1)
    with pytest.raises(RepositoryInputError) as limit_error:
        limited.create(SnapshotRequestV1(run_id=RUN_ID, repository_path="demo"))
    assert limit_error.value.code == "SNAPSHOT_LIMIT_EXCEEDED"


def test_rejects_hardlinks_and_per_file_budget_before_publication(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    repository = _repository(root)
    os.link(repository / "alembic.ini", repository / "hardlink.ini")
    service = _service(root, tmp_path / "artifacts")

    with pytest.raises(RepositoryInputError) as hardlink_error:
        service.create(SnapshotRequestV1(run_id=RUN_ID, repository_path="demo"))
    assert hardlink_error.value.code == "REPOSITORY_FILE_UNSUPPORTED"

    (repository / "hardlink.ini").unlink()
    (repository / "large.txt").write_text("too large")
    limited = _service(root, tmp_path / "limited", max_file_bytes=2)
    with pytest.raises(RepositoryInputError) as limit_error:
        limited.create(SnapshotRequestV1(run_id=RUN_ID, repository_path="demo"))
    assert limit_error.value.code == "SNAPSHOT_LIMIT_EXCEEDED"
    assert not (tmp_path / "limited" / "snapshots").exists()


def test_range_rejects_invalid_and_non_ancestor_refs(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    repository = _repository(root)
    initial = _git(repository, "rev-parse", "HEAD")
    (repository / "main.txt").write_text("main")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "main")
    main = _git(repository, "rev-parse", "HEAD")
    _git(repository, "checkout", "-q", "-b", "other", initial)
    (repository / "other.txt").write_text("other")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "other")
    other = _git(repository, "rev-parse", "HEAD")
    service = _service(root, tmp_path / "artifacts")

    with pytest.raises(RepositoryInputError) as invalid:
        service.create(
            SnapshotRequestV1(
                run_id=RUN_ID,
                repository_path="demo",
                diff_mode="RANGE",
                base_ref="does-not-exist",
                head_ref=other,
            )
        )
    assert invalid.value.code == "GIT_SELECTOR_INVALID"

    with pytest.raises(RepositoryInputError) as nonlinear:
        service.create(
            SnapshotRequestV1(
                run_id=RUN_ID,
                repository_path="demo",
                diff_mode="RANGE",
                base_ref=main,
                head_ref=other,
            )
        )
    assert nonlinear.value.code == "GIT_RANGE_NOT_LINEAR"


def test_snapshot_reader_is_manifest_bound_and_detects_tampering(tmp_path: Path) -> None:
    root = tmp_path / "repositories"
    root.mkdir()
    _repository(root)
    artifacts = tmp_path / "artifacts"
    snapshot = _service(root, artifacts).create(
        SnapshotRequestV1(run_id=RUN_ID, repository_path="demo")
    )
    reader = SnapshotReader(
        artifacts, lambda snapshot_id: snapshot if snapshot_id == snapshot.id else None
    )

    text = reader.read_text(snapshot.id, "alembic.ini", max_bytes=1024)

    assert text.text == "[alembic]\nscript_location = migrations\n"
    assert text.evidence_id.startswith("evidence:")
    with pytest.raises(RepositoryInputError):
        reader.read_text(snapshot.id, "../outside", max_bytes=1024)

    artifact_file = artifacts / "snapshots" / snapshot.content_hash / "tree" / "alembic.ini"
    artifact_file.write_text("tampered")
    with pytest.raises(RepositoryInputError) as tampered:
        reader.read_text(snapshot.id, "alembic.ini", max_bytes=1024)
    assert tampered.value.code == "SNAPSHOT_INTEGRITY_FAILED"
