"""Versioned audit run API schemas."""

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shadowops.domain.runs import RunState, StepStatus


class DiffMode(StrEnum):
    WORKING_TREE = "WORKING_TREE"
    RANGE = "RANGE"


class CreateAuditRunRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_path: str = Field(min_length=1, max_length=4096)
    diff_mode: DiffMode = DiffMode.WORKING_TREE
    base_ref: str | None = Field(default=None, min_length=1, max_length=512)
    head_ref: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("repository_path", "base_ref", "head_ref")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or "\x00" in normalized:
            raise ValueError("value must contain safe non-empty text")
        return normalized

    @field_validator("repository_path")
    @classmethod
    def validate_relative_repository_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
            raise ValueError("repository_path must be a safe relative path")
        return value

    @model_validator(mode="after")
    def validate_diff_selection(self) -> Self:
        if self.diff_mode is DiffMode.RANGE and (self.base_ref is None or self.head_ref is None):
            raise ValueError("RANGE mode requires both base_ref and head_ref")
        if self.diff_mode is DiffMode.WORKING_TREE and (
            self.base_ref is not None or self.head_ref is not None
        ):
            raise ValueError("WORKING_TREE mode does not accept range refs")
        return self


class CancelAuditRunRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class AuditRunViewV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    id: UUID
    state: RunState
    version: int = Field(ge=1)
    execution_profile: str = "m3.fake-agent.v1"
    failure_code: str | None = None
    cancel_requested_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    links: dict[str, str] = Field(default_factory=dict)


class RunTimelineEventV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    state: RunState
    at: datetime
    step_key: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    status: StepStatus | None = None
    handler_version: str | None = None
    error_code: str | None = None


class RunStepViewV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_key: str
    attempt: int = Field(ge=1)
    status: StepStatus
    handler_version: str
    started_at: datetime
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None


class RunTimelineViewV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    run_id: UUID
    run_version: int = Field(ge=1)
    terminal: bool
    events: list[RunTimelineEventV1]
    current_step: RunStepViewV1 | None = None
