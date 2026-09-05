"""Versioned contracts for Agent planning and trace persistence."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapabilityName(StrEnum):
    PROVISION_SHADOW_DB = "provision_shadow_db"
    UPGRADE_BASELINE = "upgrade_baseline"
    APPLY_TARGET_MIGRATIONS = "apply_target_migrations"
    LOAD_TEST_DATA = "load_test_data"
    RUN_SMOKE_CHECKS = "run_smoke_checks"
    VERIFY_ROLLBACK_ROUNDTRIP = "verify_rollback_roundtrip"
    COLLECT_EVIDENCE = "collect_evidence"
    CLEANUP_SHADOW_ENVIRONMENT = "cleanup_shadow_environment"


class ReadOnlyToolName(StrEnum):
    DISCOVER_MIGRATIONS = "discover_migrations"
    READ_REVISION = "read_revision"
    GET_STATIC_FINDINGS = "get_static_findings"
    DESCRIBE_SHADOW_CAPABILITIES = "describe_shadow_capabilities"
    GET_TEST_DATA_PROFILE = "get_test_data_profile"
    GET_AUDIT_PLAN = "get_audit_plan"
    GET_STEP_RESULT = "get_step_result"
    GET_EVIDENCE = "get_evidence"
    INSPECT_SCHEMA_DIFF = "inspect_schema_diff"


ShortStatement = Annotated[str, Field(min_length=1, max_length=500)]
EvidenceReference = Annotated[str, Field(min_length=1, max_length=255)]


class PlanStepV1(StrictContract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    capability: CapabilityName
    depends_on: tuple[str, ...] = Field(default=(), max_length=16)
    timeout_seconds: int = Field(ge=1, le=600)
    required: bool
    reason: ShortStatement
    evidence_refs: tuple[EvidenceReference, ...] = Field(default=(), max_length=32)


class AuditPlanV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    objective: ShortStatement
    steps: tuple[PlanStepV1, ...] = Field(min_length=1, max_length=32)
    coverage_gaps: tuple[ShortStatement, ...] = Field(default=(), max_length=16)
    assumptions: tuple[ShortStatement, ...] = Field(default=(), max_length=16)


class CapabilitySpecV1(StrictContract):
    name: CapabilityName
    version: str
    mandatory: bool
    max_timeout_seconds: int = Field(ge=1, le=600)
    prerequisites: tuple[CapabilityName, ...] = ()
    effect_scope: Literal["future_shadow_environment"] = "future_shadow_environment"


class ToolObservationV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    tool_name: ReadOnlyToolName
    tool_version: str
    data: dict[str, Any]
    evidence_ids: tuple[str, ...] = ()


class PlannerRequestV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    phase: Literal["PLANNER"] = "PLANNER"
    prompt_version: str
    tool_schema_version: str
    input_hash: str
    observations: tuple[ToolObservationV1, ...]
    repair_errors: tuple[str, ...] = ()
    prior_output_hash: str | None = None
    prior_output: str | None = Field(default=None, max_length=262_144)


class ProviderResponseV1(StrictContract):
    text: str
    response_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(default=0, ge=0)


class AgentInvocationV1(StrictContract):
    id: UUID
    run_id: UUID
    phase: Literal["PLANNER", "REPORTER"] = "PLANNER"
    provider: str
    model: str
    prompt_version: str
    tool_schema_version: str
    input_hash: str
    output_hash: str | None = None
    status: Literal["SUCCEEDED", "FAILED"]
    repair_attempts: int = Field(ge=0, le=1)
    error_code: str | None = None
    error_detail: str | None = None
    provider_response_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    started_at: datetime
    completed_at: datetime


class ToolCallV1(StrictContract):
    id: UUID
    invocation_id: UUID
    run_id: UUID
    sequence: int = Field(ge=1)
    tool_name: ReadOnlyToolName
    tool_version: str
    arguments_hash: str
    result_hash: str
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    duration_ms: int = Field(ge=0)
    correlation_id: str
    observation: ToolObservationV1


class AuditPlanRecordV1(StrictContract):
    id: UUID
    run_id: UUID
    invocation_id: UUID
    input_hash: str
    plan: AuditPlanV1
    created_at: datetime


class PlanningResultV1(StrictContract):
    invocation: AgentInvocationV1
    tool_calls: tuple[ToolCallV1, ...]
    plan: AuditPlanRecordV1 | None = None
