"""Persist M6 Reporter metadata and immutable risk reports.

Revision ID: 0008_risk_reports
Revises: 0007_dynamic_evidence
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_risk_reports"
down_revision: str | Sequence[str] | None = "0007_dynamic_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_invocations", sa.Column("provider_response_id", sa.String(128)))
    op.add_column("agent_invocations", sa.Column("input_tokens", sa.Integer()))
    op.add_column("agent_invocations", sa.Column("output_tokens", sa.Integer()))
    op.add_column(
        "agent_invocations",
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "risk_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("invocation_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False),
        sa.Column("final_risk", sa.String(16), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invocation_id"], ["agent_invocations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["audit_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_hash"),
        sa.UniqueConstraint("run_id"),
    )


def downgrade() -> None:
    op.drop_table("risk_reports")
    op.drop_column("agent_invocations", "latency_ms")
    op.drop_column("agent_invocations", "output_tokens")
    op.drop_column("agent_invocations", "input_tokens")
    op.drop_column("agent_invocations", "provider_response_id")
