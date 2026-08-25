"""SQLAlchemy mappings for reliable audit runs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
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
