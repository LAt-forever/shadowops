"""Versioned contracts shared by discovery services and future Agent tools."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SnapshotRequestV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    repository_path: str
    diff_mode: Literal["WORKING_TREE", "RANGE"] = "WORKING_TREE"
    base_ref: str | None = None
    head_ref: str | None = None


class GitChangeV1(StrictContract):
    path: str
    status: str
    content_hash: str | None = None


class GitSelectionV1(StrictContract):
    diff_mode: Literal["WORKING_TREE", "RANGE"]
    base_commit: str | None = None
    head_commit: str
    dirty_diff_hash: str | None = None
    files: tuple[str, ...]
    changes: tuple[GitChangeV1, ...] = ()


class ManifestFileV1(StrictContract):
    path: str
    size: int = Field(ge=0)
    mode: str
    sha256: str


class SnapshotManifestV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    files: tuple[ManifestFileV1, ...]


class RepoSnapshotV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    id: UUID
    run_id: UUID
    source_path_hash: str
    diff_mode: Literal["WORKING_TREE", "RANGE"]
    base_commit: str | None = None
    head_commit: str
    dirty_diff_hash: str | None = None
    content_hash: str
    artifact_uri: str
    file_count: int = Field(gt=0)
    total_bytes: int = Field(ge=0)
    changed_paths: tuple[GitChangeV1, ...] = ()
    created_at: datetime


class SnapshotTextV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    snapshot_id: UUID
    relative_path: str
    text: str
    content_hash: str
    evidence_id: str


class UnsupportedReasonV1(StrictContract):
    code: str
    detail: str
    relative_path: str | None = None


class RevisionNodeV1(StrictContract):
    revision: str
    parent_ids: tuple[str, ...]
    relative_path: str
    change_kind: Literal["UNCHANGED", "NEW", "MODIFIED"]
    has_upgrade: bool
    has_downgrade: bool
    content_hash: str
    evidence_id: str


class RevisionGraphV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    id: UUID
    run_id: UUID
    snapshot_id: UUID
    diff_mode: Literal["WORKING_TREE", "RANGE"]
    base_commit: str | None = None
    head_commit: str
    nodes: tuple[RevisionNodeV1, ...]
    heads: tuple[str, ...]
    baseline_revision: str | None = None
    target_chain: tuple[str, ...]
    changed_revisions: tuple[str, ...]
    supported: bool
    unsupported_reasons: tuple[UnsupportedReasonV1, ...]
    created_at: datetime
