"""SQLAlchemy mappings for reliable audit runs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuditRunModel(Base):
    __tablename__ = "audit_runs"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_audit_runs_version_positive"),
        Index("ix_audit_runs_state_updated", "state", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    repository_path: Mapped[str] = mapped_column(Text)
    diff_mode: Mapped[str] = mapped_column(String(32))
    base_ref: Mapped[str | None] = mapped_column(String(512))
    head_ref: Mapped[str | None] = mapped_column(String(512))
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    cleanup_status: Mapped[str] = mapped_column(String(32))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunStepModel(Base):
    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_key", name="uq_run_steps_run_step_key"),
        CheckConstraint("generation >= 1", name="ck_run_steps_generation_positive"),
        CheckConstraint("attempt >= 1", name="ck_run_steps_attempt_positive"),
        Index("ix_run_steps_active_lease", "status", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(255))
    from_state: Mapped[str] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32))
    generation: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    expected_run_version: Mapped[int] = mapped_column(Integer)
    resulting_run_version: Mapped[int | None] = mapped_column(Integer)
    handler_version: Mapped[str] = mapped_column(String(64))
    worker_id: Mapped[str | None] = mapped_column(String(255))
    claim_token: Mapped[UUID | None] = mapped_column(Uuid)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            "topic",
            name="uq_outbox_aggregate_version_topic",
        ),
        Index("ix_outbox_events_available", "published_at", "available_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    aggregate_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False
    )
    aggregate_version: Mapped[int] = mapped_column(Integer)
    topic: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RepoSnapshotModel(Base):
    __tablename__ = "repo_snapshots"
    __table_args__ = (
        CheckConstraint("file_count > 0", name="ck_repo_snapshots_file_count_positive"),
        CheckConstraint("total_bytes >= 0", name="ck_repo_snapshots_total_bytes_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    schema_version: Mapped[str] = mapped_column(String(16))
    source_path_hash: Mapped[str] = mapped_column(String(64))
    diff_mode: Mapped[str] = mapped_column(String(32))
    base_commit: Mapped[str | None] = mapped_column(String(40))
    head_commit: Mapped[str] = mapped_column(String(40))
    dirty_diff_hash: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    artifact_uri: Mapped[str] = mapped_column(String(255))
    file_count: Mapped[int] = mapped_column(Integer)
    total_bytes: Mapped[int] = mapped_column(Integer)
    changed_paths: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RevisionGraphModel(Base):
    __tablename__ = "revision_graphs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repo_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(16))
    supported: Mapped[bool] = mapped_column(Boolean)
    nodes: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    heads: Mapped[list[str]] = mapped_column(JSON)
    baseline_revision: Mapped[str | None] = mapped_column(String(255))
    target_chain: Mapped[list[str]] = mapped_column(JSON)
    changed_revisions: Mapped[list[str]] = mapped_column(JSON)
    unsupported_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StaticReportModel(Base):
    __tablename__ = "static_reports"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repo_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(16))
    ruleset_version: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(16))
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentInvocationModel(Base):
    __tablename__ = "agent_invocations"
    __table_args__ = (UniqueConstraint("run_id", "phase", name="uq_agent_run_phase"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    tool_schema_version: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    repair_attempts: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentToolCallModel(Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        UniqueConstraint("invocation_id", "sequence", name="uq_agent_tool_call_sequence"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    invocation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_invocations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(64))
    tool_version: Mapped[str] = mapped_column(String(32))
    arguments_hash: Mapped[str] = mapped_column(String(64))
    result_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    duration_ms: Mapped[int] = mapped_column(Integer)
    correlation_id: Mapped[str] = mapped_column(String(64))
    observation: Mapped[dict[str, Any]] = mapped_column(JSON)


class AuditPlanModel(Base):
    __tablename__ = "audit_plans"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    invocation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_invocations.id", ondelete="CASCADE"), nullable=False
    )
    input_hash: Mapped[str] = mapped_column(String(64))
    plan: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ShadowEnvironmentModel(Base):
    __tablename__ = "shadow_environments"
    __table_args__ = (
        UniqueConstraint("run_id", "generation", name="uq_shadow_run_generation"),
        CheckConstraint("generation >= 1", name="ck_shadow_generation_positive"),
        Index("ix_shadow_status_lease", "status", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    postgres_container_id: Mapped[str] = mapped_column(String(128))
    network_id: Mapped[str] = mapped_column(String(128))
    volume_name: Mapped[str] = mapped_column(String(255))
    snapshot_volume_name: Mapped[str] = mapped_column(String(255))
    postgres_image: Mapped[str] = mapped_column(String(255))
    postgres_image_id: Mapped[str] = mapped_column(String(80))
    runner_image: Mapped[str] = mapped_column(String(255))
    runner_image_id: Mapped[str] = mapped_column(String(80))
    database_password: Mapped[str] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunnerExecutionModel(Base):
    __tablename__ = "runner_executions"
    __table_args__ = (
        UniqueConstraint("environment_id", "action", name="uq_runner_environment_action"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    environment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("shadow_environments.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(32))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvidenceItemModel(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        UniqueConstraint("execution_id", "kind", "sha256", name="uq_evidence_execution_kind_hash"),
        Index("ix_evidence_items_run_created", "run_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False
    )
    execution_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runner_executions.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    producer: Mapped[str] = mapped_column(String(128))
    observation_scope: Mapped[str] = mapped_column(String(32))
    artifact_uri: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(64))
    redaction_status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
