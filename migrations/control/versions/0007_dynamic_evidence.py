"""Persist M5 content-addressed dynamic evidence metadata.

Revision ID: 0007_dynamic_evidence
Revises: 0006_dynamic_sandbox
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_dynamic_evidence"
down_revision: str | Sequence[str] | None = "0006_dynamic_sandbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("producer", sa.String(length=128), nullable=False),
        sa.Column("observation_scope", sa.String(length=32), nullable=False),
        sa.Column("artifact_uri", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("redaction_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["runner_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["audit_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "execution_id", "kind", "sha256", name="uq_evidence_execution_kind_hash"
        ),
    )
    op.create_index("ix_evidence_items_run_created", "evidence_items", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_evidence_items_run_created", table_name="evidence_items")
    op.drop_table("evidence_items")
