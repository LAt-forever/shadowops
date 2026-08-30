from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("THIS IS NOT VALID SQL")


def downgrade():
    pass
