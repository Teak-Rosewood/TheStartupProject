"""trends: candidates are scored per region

Revision ID: trends_0002
Revises: trends_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "trends_0002"
down_revision = "trends_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tr_candidates", sa.Column("region", sa.Text(), server_default="", nullable=False)
    )
    op.drop_constraint("uq_tr_candidates_org_day_label", "tr_candidates", type_="unique")
    op.create_unique_constraint(
        "uq_tr_candidates_org_day_label_region",
        "tr_candidates",
        ["org_id", "day", "label", "region"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_tr_candidates_org_day_label_region", "tr_candidates", type_="unique")
    op.execute("DELETE FROM tr_candidates WHERE region <> ''")
    op.create_unique_constraint(
        "uq_tr_candidates_org_day_label", "tr_candidates", ["org_id", "day", "label"]
    )
    op.drop_column("tr_candidates", "region")
