"""Bounded Git selection without invoking repository-controlled programs."""

import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Never

from shadowops.domain.errors import RepositoryInputError
from shadowops.repository.contracts import GitChangeV1, GitSelectionV1
from shadowops.repository.security import is_excluded, safe_relative_path

_GIT_PREFIX = (
    "git",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "diff.external=",
    "-c",
    "diff.noprefix=false",
)


class GitRepository:
    def __init__(
        self,
        path: Path,
        *,
        max_file_bytes: int = 5 * 1024 * 1024,
        read_chunk_bytes: int = 1024 * 1024,
    ) -> None:
        self.path = path
        self._max_file_bytes = max_file_bytes
        self._read_chunk_bytes = read_chunk_bytes

    def select(
        self,
        diff_mode: str,
        *,
        base_ref: str | None = None,
        head_ref: str | None = None,
    ) -> GitSelectionV1:
        if diff_mode == "WORKING_TREE":
            if base_ref is not None or head_ref is not None:
                self._fail("GIT_SELECTOR_INVALID", "Working-tree selection does not accept refs")
            return self._working_tree()
        if diff_mode == "RANGE":
            if not base_ref or not head_ref:
                self._fail("GIT_SELECTOR_INVALID", "Range selection requires two refs")
            return self._range(base_ref, head_ref)
        self._fail("GIT_SELECTOR_INVALID", "Unsupported Git selector")

    def read_blob(self, object_id: str, *, max_bytes: int) -> bytes:
        size_text = self._run_text("cat-file", "-s", object_id).strip()
        try:
            size = int(size_text)
        except ValueError as exc:
            raise RepositoryInputError("GIT_OBJECT_INVALID", "Git object is invalid") from exc
        if size > max_bytes:
            self._fail("SNAPSHOT_LIMIT_EXCEEDED", "Snapshot file budget exceeded")
        data = self._run("cat-file", "blob", object_id)
        if len(data) != size:
            self._fail("GIT_OBJECT_INVALID", "Git object is invalid")
        return data

    def tree_entries(self, commit: str) -> tuple[tuple[str, str, str], ...]:
        raw = self._run("ls-tree", "-rz", "--full-tree", commit)
        entries: list[tuple[str, str, str]] = []
        for record in raw.split(b"\0"):
            if not record:
                continue
            try:
                metadata, raw_path = record.split(b"\t", 1)
                mode, object_type, object_id = metadata.decode("ascii").split(" ", 2)
                path_text = raw_path.decode("utf-8", errors="strict")
                relative = safe_relative_path(path_text)
            except (ValueError, UnicodeDecodeError) as exc:
                raise RepositoryInputError("GIT_OBJECT_INVALID", "Git tree is invalid") from exc
            if is_excluded(relative):
                continue
            if object_type != "blob" or mode not in {"100644", "100755"}:
                self._fail("REPOSITORY_FILE_UNSUPPORTED", "Git tree contains unsupported entries")
            entries.append((relative.as_posix(), object_id, mode))
        return tuple(sorted(entries))

    def _working_tree(self) -> GitSelectionV1:
        head = self._resolve_commit("HEAD")
        raw_files = self._run("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        files = self._decode_paths(raw_files)
        changes = self._working_changes(files)
        canonical = json.dumps(
            [change.model_dump(mode="json") for change in changes],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return GitSelectionV1(
            diff_mode="WORKING_TREE",
            head_commit=head,
            dirty_diff_hash=hashlib.sha256(canonical).hexdigest(),
            files=files,
            changes=changes,
        )

    def _range(self, base_ref: str, head_ref: str) -> GitSelectionV1:
        base = self._resolve_commit(base_ref)
        head = self._resolve_commit(head_ref)
        result = self._run_result("merge-base", "--is-ancestor", base, head, check=False)
        if result.returncode == 1:
            self._fail("GIT_RANGE_NOT_LINEAR", "Range base is not an ancestor of head")
        if result.returncode != 0:
            self._fail("GIT_OBJECT_INVALID", "Git range could not be verified")
        entries = self.tree_entries(head)
        changes = self._range_changes(base, head)
        return GitSelectionV1(
            diff_mode="RANGE",
            base_commit=base,
            head_commit=head,
            files=tuple(path for path, _, _ in entries),
            changes=changes,
        )

    def _working_changes(self, files: tuple[str, ...]) -> tuple[GitChangeV1, ...]:
        raw = self._run("status", "--porcelain=v1", "-z", "--untracked-files=all")
        records = raw.split(b"\0")
        changes: list[GitChangeV1] = []
        index = 0
        file_set = set(files)
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            try:
                status = record[:2].decode("ascii")
                path = record[3:].decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise RepositoryInputError("GIT_OBJECT_INVALID", "Git status is invalid") from exc
            if "R" in status or "C" in status:
                index += 1
            relative = safe_relative_path(path)
            if is_excluded(relative):
                continue
            content_hash = None
            if relative.as_posix() in file_set:
                content_hash = self._hash_worktree_file(relative)
            changes.append(
                GitChangeV1(
                    path=relative.as_posix(), status=status.strip(), content_hash=content_hash
                )
            )
        return tuple(sorted(changes, key=lambda change: (change.path, change.status)))

    def _range_changes(self, base: str, head: str) -> tuple[GitChangeV1, ...]:
        raw = self._run("diff", "--no-ext-diff", "--name-status", "-z", "--no-renames", base, head)
        parts = [part for part in raw.split(b"\0") if part]
        changes: list[GitChangeV1] = []
        for index in range(0, len(parts), 2):
            try:
                status = parts[index].decode("ascii")
                relative = safe_relative_path(parts[index + 1].decode("utf-8", errors="strict"))
            except (IndexError, UnicodeDecodeError) as exc:
                raise RepositoryInputError("GIT_OBJECT_INVALID", "Git diff is invalid") from exc
            if not is_excluded(relative):
                changes.append(GitChangeV1(path=relative.as_posix(), status=status))
        return tuple(sorted(changes, key=lambda change: (change.path, change.status)))

    def _hash_worktree_file(self, relative: PurePosixPath) -> str | None:
        path = self.path.joinpath(*relative.parts)
        try:
            if not path.is_file() or path.is_symlink():
                return None
            if path.stat().st_size > self._max_file_bytes:
                self._fail("SNAPSHOT_LIMIT_EXCEEDED", "Snapshot file budget exceeded")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as source:
                while chunk := source.read(self._read_chunk_bytes):
                    size += len(chunk)
                    if size > self._max_file_bytes:
                        self._fail("SNAPSHOT_LIMIT_EXCEEDED", "Snapshot file budget exceeded")
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError as exc:
            raise RepositoryInputError(
                "REPOSITORY_CHANGED_DURING_SNAPSHOT", "Repository changed during selection"
            ) from exc

    def _decode_paths(self, raw: bytes) -> tuple[str, ...]:
        result: list[str] = []
        for item in raw.split(b"\0"):
            if not item:
                continue
            try:
                relative = safe_relative_path(item.decode("utf-8", errors="strict"))
            except UnicodeDecodeError as exc:
                raise RepositoryInputError("GIT_OBJECT_INVALID", "Git path is invalid") from exc
            if not is_excluded(relative):
                result.append(relative.as_posix())
        return tuple(sorted(set(result)))

    def _resolve_commit(self, ref: str) -> str:
        if not ref or "\x00" in ref or ref.startswith("-"):
            self._fail("GIT_SELECTOR_INVALID", "Git ref is invalid")
        result = self._run_result(
            "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", check=False
        )
        if result.returncode != 0:
            self._fail("GIT_SELECTOR_INVALID", "Git ref does not resolve to a commit")
        try:
            commit = result.stdout.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise RepositoryInputError("GIT_OBJECT_INVALID", "Git object is invalid") from exc
        if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
            self._fail("GIT_OBJECT_INVALID", "Git object is invalid")
        return commit

    def _run_text(self, *args: str) -> str:
        try:
            return self._run(*args).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RepositoryInputError("GIT_OBJECT_INVALID", "Git output is invalid") from exc

    def _run(self, *args: str) -> bytes:
        result = self._run_result(*args, check=False)
        if result.returncode != 0:
            self._fail("GIT_OBJECT_INVALID", "Git repository data could not be read")
        return result.stdout

    def _run_result(self, *args: str, check: bool) -> subprocess.CompletedProcess[bytes]:
        environment = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        try:
            return subprocess.run(
                [
                    *_GIT_PREFIX,
                    "-c",
                    f"safe.directory={self.path}",
                    "-C",
                    str(self.path),
                    *args,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
                check=check,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RepositoryInputError(
                "GIT_OBJECT_INVALID", "Git repository data could not be read"
            ) from exc

    @staticmethod
    def _fail(code: str, detail: str) -> Never:
        raise RepositoryInputError(code, detail)
