import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("nickname", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("users", "nickname")
