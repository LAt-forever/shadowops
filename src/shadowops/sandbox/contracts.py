"""Versioned contracts between the trusted orchestrator and fixed Runner."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shadowops.evidence.contracts import EvidenceItemV1


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunnerAction(StrEnum):
    UPGRADE_BASELINE = "UPGRADE_BASELINE"
    APPLY_TARGET = "APPLY_TARGET"
    LOAD_TEST_DATA = "LOAD_TEST_DATA"
    RUN_SMOKE_CHECKS = "RUN_SMOKE_CHECKS"
    VERIFY_ROLLBACK_ROUNDTRIP = "VERIFY_ROLLBACK_ROUNDTRIP"


class RunnerStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RunnerRequestV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    action: RunnerAction
    revision: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_.-]+$")
    baseline_revision: str | None = Field(
        default=None, max_length=255, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    database_alias: Literal["shadow-postgres"] = "shadow-postgres"
    statement_timeout_ms: int = Field(ge=100, le=120_000)
    output_limit_bytes: int = Field(ge=1_024, le=262_144)

    @model_validator(mode="after")
    def validate_action_fields(self) -> Self:
        baseline_actions = {
            RunnerAction.LOAD_TEST_DATA,
            RunnerAction.VERIFY_ROLLBACK_ROUNDTRIP,
        }
        if self.action in baseline_actions and self.baseline_revision is None:
            raise ValueError("action requires baseline_revision")
        if self.action not in baseline_actions and self.baseline_revision is not None:
            raise ValueError("baseline_revision is not valid for this action")
        return self


class BoundedArtifactV1(StrictContract):
    media_type: Literal["text/plain"] = "text/plain"
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    truncated: bool
    text: str

    @model_validator(mode="after")
    def validate_content_identity(self) -> Self:
        content = self.text.encode()
        if len(content) != self.byte_count or hashlib.sha256(content).hexdigest() != self.sha256:
            raise ValueError("artifact size or hash does not match its bounded text")
        return self


class ObservationKind(StrEnum):
    SEED_SUMMARY = "SEED_SUMMARY"
    SCHEMA_FINGERPRINT = "SCHEMA_FINGERPRINT"
    SMOKE_SUMMARY = "SMOKE_SUMMARY"
    ROLLBACK_ROUNDTRIP = "ROLLBACK_ROUNDTRIP"


class RunnerObservationV1(StrictContract):
    kind: ObservationKind
    scope: Literal["observed_in_shadow"] = "observed_in_shadow"
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    data: dict[str, object]

    @model_validator(mode="after")
    def validate_content_identity(self) -> Self:
        encoded = json.dumps(
            self.data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        if len(encoded) > 65_536 or hashlib.sha256(encoded).hexdigest() != self.sha256:
            raise ValueError("observation size or hash does not match its canonical data")
        return self


class RunnerResultV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    action: RunnerAction
    status: RunnerStatus
    error_code: str | None = Field(default=None, max_length=64)
    error_detail: str | None = Field(default=None, max_length=500)
    current_revision: str | None = Field(default=None, max_length=255)
    duration_ms: int = Field(ge=0)
    coverage_gaps: tuple[str, ...] = Field(default=(), max_length=64)
    observations: tuple[RunnerObservationV1, ...] = Field(default=(), max_length=16)
    stdout: BoundedArtifactV1
    stderr: BoundedArtifactV1


class ShadowEnvironmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLEANED = "CLEANED"
    CLEANUP_FAILED = "CLEANUP_FAILED"


class ShadowEnvironmentV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    id: UUID
    run_id: UUID
    generation: int = Field(ge=1)
    status: ShadowEnvironmentStatus
    postgres_container_id: str = Field(max_length=128)
    network_id: str = Field(max_length=128)
    volume_name: str = Field(max_length=255)
    snapshot_volume_name: str = Field(max_length=255)
    postgres_image: str = Field(max_length=255)
    postgres_image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    runner_image: str = Field(max_length=255)
    runner_image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    lease_expires_at: datetime
    created_at: datetime
    cleaned_at: datetime | None = None


class RunnerExecutionV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    id: UUID
    environment_id: UUID
    run_id: UUID
    generation: int = Field(ge=1)
    request: RunnerRequestV1
    result: RunnerResultV1
    created_at: datetime


class DynamicAuditViewV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    generation: int = Field(ge=1)
    environment: ShadowEnvironmentV1
    executions: tuple[RunnerExecutionV1, ...]
    evidence_items: tuple[EvidenceItemV1, ...] = ()


@dataclass(frozen=True)
class ShadowEnvironmentLease:
    """Trusted control-plane record; the password is never serialized to Agent contracts."""

    environment: ShadowEnvironmentV1
    database_password: str
