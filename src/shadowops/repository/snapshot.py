"""Content-addressed immutable repository snapshots."""

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from shadowops.domain.errors import RepositoryInputError
from shadowops.repository.contracts import (
    GitSelectionV1,
    ManifestFileV1,
    RepoSnapshotV1,
    SnapshotManifestV1,
    SnapshotRequestV1,
    SnapshotTextV1,
)
from shadowops.repository.git import GitRepository
from shadowops.repository.security import (
    resolve_repository,
    safe_relative_path,
    validate_regular_file,
)


class SnapshotService:
    def __init__(
        self,
        repo_root: Path,
        artifact_root: Path,
        *,
        max_files: int = 10_000,
        max_file_bytes: int = 5 * 1024 * 1024,
        max_total_bytes: int = 100 * 1024 * 1024,
        read_chunk_bytes: int = 1024 * 1024,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._artifact_root = artifact_root
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes
        self._read_chunk_bytes = read_chunk_bytes
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4

    def create(
        self,
        request: SnapshotRequestV1,
        *,
        checkpoint: Callable[[], None] | None = None,
    ) -> RepoSnapshotV1:
        repository = resolve_repository(self._repo_root, request.repository_path)
        git = GitRepository(
            repository,
            max_file_bytes=self._max_file_bytes,
            read_chunk_bytes=self._read_chunk_bytes,
        )
        selection = git.select(
            request.diff_mode, base_ref=request.base_ref, head_ref=request.head_ref
        )
        if checkpoint is not None:
            checkpoint()
        if not selection.files:
            raise RepositoryInputError(
                "REPOSITORY_FILE_UNSUPPORTED", "Repository has no snapshot files"
            )
        snapshots_root = self._artifact_root / "snapshots"
        snapshots_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=snapshots_root))
        try:
            tree = temporary / "tree"
            tree.mkdir()
            manifest_files = self._copy_selection(repository, git, selection, tree)
            if selection.diff_mode == "WORKING_TREE":
                verified_selection = git.select("WORKING_TREE")
                if verified_selection != selection:
                    self._changed()
            manifest = SnapshotManifestV1(files=tuple(manifest_files))
            canonical = self._canonical_manifest(manifest)
            content_hash = hashlib.sha256(canonical).hexdigest()
            (temporary / "manifest.json").write_bytes(canonical)
            self._verify_artifact(temporary, expected_hash=content_hash)
            target = snapshots_root / content_hash
            if target.exists():
                self._verify_artifact(target, expected_hash=content_hash)
            else:
                try:
                    temporary.rename(target)
                except OSError:
                    if not target.exists():
                        raise
                    self._verify_artifact(target, expected_hash=content_hash)
            source_path_hash = hashlib.sha256(str(repository).encode()).hexdigest()
            return RepoSnapshotV1(
                id=self._uuid_factory(),
                run_id=request.run_id,
                source_path_hash=source_path_hash,
                diff_mode=selection.diff_mode,
                base_commit=selection.base_commit,
                head_commit=selection.head_commit,
                dirty_diff_hash=selection.dirty_diff_hash,
                content_hash=content_hash,
                artifact_uri=f"artifact://snapshots/{content_hash}",
                file_count=len(manifest_files),
                total_bytes=sum(item.size for item in manifest_files),
                changed_paths=selection.changes,
                created_at=self._clock(),
            )
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def _copy_selection(
        self,
        repository: Path,
        git: GitRepository,
        selection: GitSelectionV1,
        tree: Path,
    ) -> list[ManifestFileV1]:
        if len(selection.files) > self._max_files:
            self._limit()
        range_objects = (
            {
                path: (object_id, mode)
                for path, object_id, mode in git.tree_entries(selection.head_commit)
            }
            if selection.diff_mode == "RANGE"
            else {}
        )
        manifest: list[ManifestFileV1] = []
        total = 0
        for path_text in selection.files:
            relative = safe_relative_path(path_text)
            if selection.diff_mode == "RANGE":
                range_entry = range_objects.get(path_text)
                if range_entry is None:
                    raise RepositoryInputError(
                        "GIT_OBJECT_INVALID", "Git tree changed during snapshot"
                    )
                object_id, mode = range_entry
                data = git.read_blob(object_id, max_bytes=self._max_file_bytes)
            else:
                data, mode = self._read_worktree_file(repository, relative)
            total += len(data)
            if total > self._max_total_bytes:
                self._limit()
            destination = tree.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            os.chmod(destination, 0o644)
            manifest.append(
                ManifestFileV1(
                    path=relative.as_posix(),
                    size=len(data),
                    mode=mode,
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            )
        return sorted(manifest, key=lambda item: item.path)

    def _read_worktree_file(self, repository: Path, relative: PurePosixPath) -> tuple[bytes, str]:
        path = repository.joinpath(*relative.parts)
        inspected = validate_regular_file(path)
        if inspected.st_size > self._max_file_bytes:
            self._limit()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise RepositoryInputError(
                "REPOSITORY_CHANGED_DURING_SNAPSHOT", "Repository changed during snapshot"
            ) from exc
        try:
            opened = validate_regular_file(path, before=os.fstat(descriptor))
            if (opened.st_dev, opened.st_ino) != (inspected.st_dev, inspected.st_ino):
                self._changed()
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(descriptor, self._read_chunk_bytes)
                if not chunk:
                    break
                size += len(chunk)
                if size > self._max_file_bytes:
                    self._limit()
                chunks.append(chunk)
            completed = os.fstat(descriptor)
            if (completed.st_dev, completed.st_ino, completed.st_size, completed.st_mtime_ns) != (
                inspected.st_dev,
                inspected.st_ino,
                inspected.st_size,
                inspected.st_mtime_ns,
            ):
                self._changed()
            mode = "100755" if completed.st_mode & stat.S_IXUSR else "100644"
            return b"".join(chunks), mode
        finally:
            os.close(descriptor)

    @staticmethod
    def _canonical_manifest(manifest: SnapshotManifestV1) -> bytes:
        return json.dumps(
            manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()

    def _verify_artifact(self, artifact: Path, *, expected_hash: str) -> SnapshotManifestV1:
        try:
            if artifact.is_symlink() or not artifact.is_dir():
                raise ValueError
            manifest_path = artifact / "manifest.json"
            validate_regular_file(manifest_path)
            canonical = manifest_path.read_bytes()
            if hashlib.sha256(canonical).hexdigest() != expected_hash:
                raise ValueError
            manifest = SnapshotManifestV1.model_validate_json(canonical)
            if canonical != self._canonical_manifest(manifest):
                raise ValueError
            for item in manifest.files:
                relative = safe_relative_path(item.path)
                path = artifact.joinpath("tree", *relative.parts)
                inspected = validate_regular_file(path)
                if (
                    inspected.st_size != item.size
                    or hashlib.sha256(path.read_bytes()).hexdigest() != item.sha256
                ):
                    raise ValueError
            return manifest
        except (OSError, ValueError) as exc:
            raise RepositoryInputError(
                "SNAPSHOT_INTEGRITY_FAILED", "Snapshot artifact integrity check failed"
            ) from exc

    @staticmethod
    def _limit() -> None:
        raise RepositoryInputError("SNAPSHOT_LIMIT_EXCEEDED", "Snapshot budget exceeded")

    @staticmethod
    def _changed() -> None:
        raise RepositoryInputError(
            "REPOSITORY_CHANGED_DURING_SNAPSHOT", "Repository changed during snapshot"
        )


class SnapshotReader:
    def __init__(
        self,
        artifact_root: Path,
        snapshot_lookup: Callable[[UUID], RepoSnapshotV1 | None],
    ) -> None:
        self._artifact_root = artifact_root
        self._snapshot_lookup = snapshot_lookup

    def read_text(self, snapshot_id: UUID, relative_path: str, *, max_bytes: int) -> SnapshotTextV1:
        snapshot = self._snapshot_lookup(snapshot_id)
        if snapshot is None:
            raise RepositoryInputError("SNAPSHOT_INTEGRITY_FAILED", "Snapshot was not found")
        relative = safe_relative_path(relative_path)
        artifact = self._artifact_root / "snapshots" / snapshot.content_hash
        try:
            manifest_data = (artifact / "manifest.json").read_bytes()
            if hashlib.sha256(manifest_data).hexdigest() != snapshot.content_hash:
                raise ValueError
            manifest = SnapshotManifestV1.model_validate_json(manifest_data)
        except (OSError, ValueError) as exc:
            raise RepositoryInputError(
                "SNAPSHOT_INTEGRITY_FAILED", "Snapshot manifest is invalid"
            ) from exc
        entry = next((item for item in manifest.files if item.path == relative.as_posix()), None)
        if entry is None or entry.size > max_bytes:
            raise RepositoryInputError("SNAPSHOT_LIMIT_EXCEEDED", "Snapshot read budget exceeded")
        source_path = artifact.joinpath("tree", *relative.parts)
        validate_regular_file(source_path)
        data = source_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            raise RepositoryInputError("SNAPSHOT_INTEGRITY_FAILED", "Snapshot content is invalid")
        try:
            content = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RepositoryInputError(
                "REPOSITORY_FILE_UNSUPPORTED", "Snapshot file is not UTF-8 text"
            ) from exc
        evidence = hashlib.sha256(
            f"{snapshot.content_hash}:{entry.path}:{entry.sha256}".encode()
        ).hexdigest()
        return SnapshotTextV1(
            snapshot_id=snapshot_id,
            relative_path=entry.path,
            text=content,
            content_hash=entry.sha256,
            evidence_id=f"evidence:{evidence}",
        )
