"""Allowed-root and repository file policy enforcement."""

import os
import stat
from pathlib import Path, PurePosixPath

from shadowops.domain.errors import RepositoryInputError

EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
        "build",
        ".aws",
        ".azure",
        ".config/gcloud",
    }
)
EXCLUDED_FILENAMES = frozenset({"id_rsa", "id_ed25519", "credentials"})


def safe_relative_path(value: str) -> PurePosixPath:
    if not value or "\x00" in value:
        raise RepositoryInputError("REPOSITORY_OUTSIDE_ALLOWED_ROOT", "Invalid repository path")
    raw_parts = value.split("/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts):
        raise RepositoryInputError("REPOSITORY_OUTSIDE_ALLOWED_ROOT", "Invalid repository path")
    return path


def resolve_repository(root: Path, repository_path: str) -> Path:
    relative = safe_relative_path(repository_path)
    try:
        resolved_root = root.resolve(strict=True)
        candidate_entry = resolved_root.joinpath(*relative.parts)
        current = resolved_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise RepositoryInputError(
                    "REPOSITORY_SYMLINK_REJECTED", "Repository symlinks are not accepted"
                )
        candidate = candidate_entry.resolve(strict=True)
    except RepositoryInputError:
        raise
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        raise RepositoryInputError("REPOSITORY_NOT_FOUND", "Repository was not found") from exc
    if not candidate.is_relative_to(resolved_root):
        raise RepositoryInputError(
            "REPOSITORY_OUTSIDE_ALLOWED_ROOT", "Repository is outside the allowed root"
        )
    if not candidate.is_dir():
        raise RepositoryInputError("REPOSITORY_NOT_FOUND", "Repository was not found")
    git_control = candidate / ".git"
    if not git_control.exists() or git_control.is_symlink():
        raise RepositoryInputError("REPOSITORY_NOT_GIT", "Repository is not a Git worktree")
    return candidate


def is_excluded(path: PurePosixPath) -> bool:
    parts = path.parts
    for index, part in enumerate(parts[:-1]):
        prefix = "/".join(parts[index : index + 2])
        if part in EXCLUDED_DIRECTORIES or prefix in EXCLUDED_DIRECTORIES:
            return True
    name = path.name
    if name == ".env.example":
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    return name in EXCLUDED_FILENAMES or name.endswith((".pem", ".key"))


def validate_regular_file(path: Path, *, before: os.stat_result | None = None) -> os.stat_result:
    try:
        inspected = before or path.lstat()
    except OSError as exc:
        raise RepositoryInputError(
            "REPOSITORY_CHANGED_DURING_SNAPSHOT", "Repository changed during snapshot"
        ) from exc
    if stat.S_ISLNK(inspected.st_mode):
        raise RepositoryInputError(
            "REPOSITORY_SYMLINK_REJECTED", "Repository symlinks are not accepted"
        )
    if not stat.S_ISREG(inspected.st_mode) or inspected.st_nlink != 1:
        raise RepositoryInputError(
            "REPOSITORY_FILE_UNSUPPORTED", "Repository contains an unsupported file"
        )
    return inspected
