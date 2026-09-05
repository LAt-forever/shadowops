"""Versioned contracts for evidence-grounded risk reports."""

import hashlib
import json
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shadowops.agent.contracts import AgentInvocationV1, ToolCallV1
from shadowops.rules.contracts import Severity


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReportFactV1(StrictContract):
    statement: str = Field(min_length=1, max_length=1_000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=16)


class ReporterDraftV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1, max_length=2_000)
    assessed_risk: Severity
    facts: tuple[ReportFactV1, ...] = Field(default=(), max_length=32)
    recommendations: tuple[str, ...] = Field(default=(), max_length=16)
    unknowns: tuple[str, ...] = Field(default=(), max_length=16)


class PolicyDecisionV1(StrictContract):
    risk_level: Severity
    requires_approval: bool
    reasons: tuple[str, ...] = Field(min_length=1, max_length=32)


class ProviderMetadataV1(StrictContract):
    provider: str
    model: str
    status: Literal["SUCCEEDED", "FAILED"]
    response_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)
    error_code: str | None = None


class RiskReportV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    id: UUID
    run_id: UUID
    invocation_id: UUID
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    report_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    final_risk: Severity
    requires_approval: bool
    policy_reasons: tuple[str, ...]
    provider_metadata: ProviderMetadataV1
    draft: ReporterDraftV1
    evidence_ids: tuple[str, ...]
    generated_by: Literal["fake", "recorded", "openai", "deterministic_fallback"]
    created_at: datetime

    @model_validator(mode="after")
    def verify_report_hash(self) -> Self:
        if self.report_hash != risk_report_hash(self):
            raise ValueError("report_hash does not match canonical report content")
        return self


class ReportingResultV1(StrictContract):
    invocation: AgentInvocationV1
    tool_calls: tuple[ToolCallV1, ...]
    report: RiskReportV1


def risk_report_hash(report: RiskReportV1 | dict[str, object]) -> str:
    value = report.model_dump(mode="json") if isinstance(report, RiskReportV1) else dict(report)
    for key in ("id", "report_hash", "created_at"):
        value.pop(key, None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
