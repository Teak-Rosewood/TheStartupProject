"""create items"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.create_table(
        "tsp_test_items", sa.Column("id", sa.Integer, primary_key=True), sa.Column("name", sa.Text)
    )


def downgrade():
    op.drop_table("tsp_test_items")
