"""Versioned audit run API schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shadowops.domain.runs import RunState


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
    execution_profile: str = "m1.noop.v1"
    cancel_requested_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    links: dict[str, str] = Field(default_factory=dict)
