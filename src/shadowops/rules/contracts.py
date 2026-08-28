"""Versioned contracts for deterministic static audit results."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shadowops.repository.contracts import UnsupportedReasonV1

Severity = Literal["INFO", "LOW", "MEDIUM", "HIGH"]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StaticFindingV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    rule_id: str
    rule_version: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    relative_path: str | None = None
    line: int = Field(ge=1)
    column: int = Field(ge=0)
    message: str
    remediation: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    observation_scope: Literal["static_source"] = "static_source"
    unknowns: tuple[str, ...] = ()


class RevisionGraphSummaryV1(StrictContract):
    supported: bool
    heads: tuple[str, ...]
    baseline_revision: str | None = None
    target_chain: tuple[str, ...]
    changed_revisions: tuple[str, ...]


class StaticReportV1(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    id: UUID
    run_id: UUID
    snapshot_id: UUID
    snapshot_hash: str
    diff_mode: Literal["WORKING_TREE", "RANGE"]
    base_commit: str | None = None
    head_commit: str
    revision_graph: RevisionGraphSummaryV1
    findings: tuple[StaticFindingV1, ...]
    unsupported_reasons: tuple[UnsupportedReasonV1, ...]
    risk_level: Severity
    ruleset_version: Literal["m2.static-rules.v1"] = "m2.static-rules.v1"
    created_at: datetime
