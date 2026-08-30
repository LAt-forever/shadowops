"""Persist M4 shadow environments and Runner results.

Revision ID: 0006_dynamic_sandbox
Revises: 0005_agent_planning
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_dynamic_sandbox"
down_revision: str | Sequence[str] | None = "0005_agent_planning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_environments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("postgres_container_id", sa.String(length=128), nullable=False),
        sa.Column("network_id", sa.String(length=128), nullable=False),
        sa.Column("volume_name", sa.String(length=255), nullable=False),
        sa.Column("snapshot_volume_name", sa.String(length=255), nullable=False),
        sa.Column("postgres_image", sa.String(length=255), nullable=False),
        sa.Column("postgres_image_id", sa.String(length=80), nullable=False),
        sa.Column("runner_image", sa.String(length=255), nullable=False),
        sa.Column("runner_image_id", sa.String(length=80), nullable=False),
        sa.Column("database_password", sa.String(length=255), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("generation >= 1", name="ck_shadow_generation_positive"),
        sa.ForeignKeyConstraint(["run_id"], ["audit_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "generation", name="uq_shadow_run_generation"),
    )
    op.create_index("ix_shadow_status_lease", "shadow_environments", ["status", "lease_expires_at"])
    op.create_table(
        "runner_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["environment_id"], ["shadow_environments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["audit_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("environment_id", "action", name="uq_runner_environment_action"),
    )


def downgrade() -> None:
    op.drop_table("runner_executions")
    op.drop_index("ix_shadow_status_lease", table_name="shadow_environments")
    op.drop_table("shadow_environments")
