import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from shadowops.domain.errors import RepositoryInputError
from shadowops.repository.alembic import AlembicDiscoveryService
from shadowops.repository.contracts import RepoSnapshotV1, SnapshotRequestV1
from shadowops.repository.snapshot import SnapshotService

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
SNAPSHOT_ID = UUID("22222222-2222-4222-8222-222222222222")
GRAPH_ID = UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 8, 26, tzinfo=UTC)


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


def _snapshot(tmp_path: Path, revisions: dict[str, str]) -> tuple[RepoSnapshotV1, Path]:
    root = tmp_path / "repositories"
    repository = root / "demo"
    versions = repository / "migrations" / "versions"
    versions.mkdir(parents=True)
    _git(repository, "init", "-q")
    (repository / "alembic.ini").write_text("[alembic]\nscript_location = %(here)s/migrations\n")
    (repository / "migrations" / "env.py").write_text(
        "raise RuntimeError('repository code was executed')\n"
    )
    for filename, source in revisions.items():
        (versions / filename).write_text(source)
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "fixture")
    artifacts = tmp_path / "artifacts"
    snapshot = SnapshotService(
        root,
        artifacts,
        clock=lambda: NOW,
        uuid_factory=lambda: SNAPSHOT_ID,
    ).create(SnapshotRequestV1(run_id=RUN_ID, repository_path="demo"))
    return snapshot, artifacts


def _discover(snapshot: RepoSnapshotV1, artifacts: Path):
    return AlembicDiscoveryService(
        artifacts,
        lambda snapshot_id: snapshot if snapshot_id == snapshot.id else None,
        clock=lambda: NOW,
        uuid_factory=lambda: GRAPH_ID,
    ).discover(snapshot.id)


def test_discovers_linear_literal_graph_without_importing_repository_code(tmp_path: Path) -> None:
    snapshot, artifacts = _snapshot(
        tmp_path,
        {
            "001.py": (
                "revision = '001'\ndown_revision = None\nbranch_labels = None\n"
                "depends_on = None\ndef upgrade(): pass\ndef downgrade(): pass\n"
            ),
            "002.py": (
                "revision = '002'\ndown_revision = '001'\nbranch_labels = None\n"
                "depends_on = None\ndef upgrade(): pass\ndef downgrade(): pass\n"
            ),
        },
    )

    graph = _discover(snapshot, artifacts)

    assert graph.supported is True
    assert graph.heads == ("002",)
    assert graph.target_chain == ("001", "002")
    assert [node.revision for node in graph.nodes] == ["001", "002"]
    assert graph.unsupported_reasons == ()


def test_dynamic_metadata_is_reported_as_unsupported_without_guessing(tmp_path: Path) -> None:
    snapshot, artifacts = _snapshot(
        tmp_path,
        {
            "dynamic.py": (
                "def make_revision(): return '001'\nrevision = make_revision()\n"
                "down_revision = None\nbranch_labels = None\ndepends_on = None\n"
            )
        },
    )

    graph = _discover(snapshot, artifacts)

    assert graph.supported is False
    assert graph.nodes == ()
    assert {reason.code for reason in graph.unsupported_reasons} == {"DYNAMIC_REVISION_METADATA"}


def test_multiple_heads_are_preserved_but_not_claimed_supported(tmp_path: Path) -> None:
    base = (
        "revision = '001'\ndown_revision = None\nbranch_labels = None\n"
        "depends_on = None\ndef upgrade(): pass\ndef downgrade(): pass\n"
    )
    child = (
        "revision = '{revision}'\ndown_revision = '001'\nbranch_labels = None\n"
        "depends_on = None\ndef upgrade(): pass\ndef downgrade(): pass\n"
    )
    snapshot, artifacts = _snapshot(
        tmp_path,
        {
            "001.py": base,
            "002.py": child.format(revision="002"),
            "003.py": child.format(revision="003"),
        },
    )

    graph = _discover(snapshot, artifacts)

    assert graph.supported is False
    assert graph.heads == ("002", "003")
    assert "MULTIPLE_REVISION_HEADS" in {reason.code for reason in graph.unsupported_reasons}


def test_broken_parent_and_merge_graph_are_reported_without_guessing(tmp_path: Path) -> None:
    snapshot, artifacts = _snapshot(
        tmp_path,
        {
            "missing.py": "revision='001'\ndown_revision='missing'\n",
            "merge.py": "revision='002'\ndown_revision=('001', 'other')\n",
        },
    )

    graph = _discover(snapshot, artifacts)

    codes = {reason.code for reason in graph.unsupported_reasons}
    assert graph.supported is False
    assert "MISSING_REVISION_PARENT" in codes
    assert "MERGE_REVISION_UNSUPPORTED" in codes


def test_cycle_is_reported_even_when_the_graph_has_no_head(tmp_path: Path) -> None:
    snapshot, artifacts = _snapshot(
        tmp_path,
        {
            "001.py": "revision='001'\ndown_revision='002'\n",
            "002.py": "revision='002'\ndown_revision='001'\n",
        },
    )

    graph = _discover(snapshot, artifacts)

    assert graph.heads == ()
    assert "REVISION_CYCLE" in {reason.code for reason in graph.unsupported_reasons}


def test_nonempty_branch_metadata_is_explicitly_unsupported(tmp_path: Path) -> None:
    snapshot, artifacts = _snapshot(
        tmp_path,
        {
            "001.py": (
                "revision='001'\ndown_revision=None\nbranch_labels=('feature',)\ndepends_on=None\n"
            )
        },
    )

    graph = _discover(snapshot, artifacts)

    assert graph.supported is False
    assert "BRANCH_LABELS_UNSUPPORTED" in {reason.code for reason in graph.unsupported_reasons}


def test_discovery_detects_snapshot_content_tampering(tmp_path: Path) -> None:
    snapshot, artifacts = _snapshot(
        tmp_path,
        {"001.py": "revision='001'\ndown_revision=None\n"},
    )
    source = (
        artifacts
        / "snapshots"
        / snapshot.content_hash
        / "tree"
        / "migrations"
        / "versions"
        / "001.py"
    )
    source.write_text("revision='tampered'\ndown_revision=None\n")

    with pytest.raises(RepositoryInputError) as error:
        _discover(snapshot, artifacts)
    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"
