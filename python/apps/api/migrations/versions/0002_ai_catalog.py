"""ai_catalog: the last verified model catalog, so routing survives restarts

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.create_table(
        "ai_catalog",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("data", JSONB, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_ai_catalog_single_row"),
    )


def downgrade() -> None:
    op.drop_table("ai_catalog")
