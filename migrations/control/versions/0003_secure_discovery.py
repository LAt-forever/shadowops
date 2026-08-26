"""Persist secure repository discovery results.

Revision ID: 0003_secure_discovery
Revises: 0002_reliable_runs
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_secure_discovery"
down_revision: str | Sequence[str] | None = "0002_reliable_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repo_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("source_path_hash", sa.String(length=64), nullable=False),
        sa.Column("diff_mode", sa.String(length=32), nullable=False),
        sa.Column("base_commit", sa.String(length=40), nullable=True),
        sa.Column("head_commit", sa.String(length=40), nullable=False),
        sa.Column("dirty_diff_hash", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_uri", sa.String(length=255), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("changed_paths", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("file_count > 0", name="ck_repo_snapshots_file_count_positive"),
        sa.CheckConstraint("total_bytes >= 0", name="ck_repo_snapshots_total_bytes_nonnegative"),
        sa.ForeignKeyConstraint(["run_id"], ["audit_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_table(
        "revision_graphs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("supported", sa.Boolean(), nullable=False),
        sa.Column("nodes", sa.JSON(), nullable=False),
        sa.Column("heads", sa.JSON(), nullable=False),
        sa.Column("baseline_revision", sa.String(length=255), nullable=True),
        sa.Column("target_chain", sa.JSON(), nullable=False),
        sa.Column("changed_revisions", sa.JSON(), nullable=False),
        sa.Column("unsupported_reasons", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["audit_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["repo_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )


def downgrade() -> None:
    op.drop_table("revision_graphs")
    op.drop_table("repo_snapshots")
