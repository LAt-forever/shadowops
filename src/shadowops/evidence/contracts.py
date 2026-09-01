"""Versioned metadata for immutable dynamic evidence artifacts."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EvidenceKind(StrEnum):
    RUNNER_STDOUT = "RUNNER_STDOUT"
    RUNNER_STDERR = "RUNNER_STDERR"
    SEED_SUMMARY = "SEED_SUMMARY"
    SCHEMA_FINGERPRINT = "SCHEMA_FINGERPRINT"
    SMOKE_SUMMARY = "SMOKE_SUMMARY"
    ROLLBACK_ROUNDTRIP = "ROLLBACK_ROUNDTRIP"
    COVERAGE_GAPS = "COVERAGE_GAPS"


class EvidenceItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    id: UUID
    run_id: UUID
    execution_id: UUID
    kind: EvidenceKind
    producer: str = Field(min_length=1, max_length=128)
    observation_scope: Literal["observed_in_shadow", "unknown_in_production"]
    artifact_uri: str = Field(pattern=r"^artifact://sha256/[a-f0-9]{64}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_count: int = Field(ge=0, le=1_048_576)
    media_type: Literal["application/json", "text/plain"]
    redaction_status: Literal["REDACTED", "NOT_REQUIRED"]
    created_at: datetime
