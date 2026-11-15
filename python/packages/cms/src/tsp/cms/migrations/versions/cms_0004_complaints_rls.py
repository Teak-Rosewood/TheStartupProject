"""row-level security on the complaints desk

Revision ID: cms_0004
Revises: cms_0003

Complaints hold complainants' names, emails and messages. Postgres now shows a transaction only the
complaints of the organization it is scoped to (tsp.db.scope_org), or all of them for maintenance
(tsp.db.scope_all_orgs). Events inherit their complaint's visibility. No data changes.
"""

from alembic import op
from tsp.db import drop_org_policy_sql, org_policy_sql

revision = "cms_0004"
down_revision = "cms_0003"
branch_labels = None
depends_on = None

EVENTS_USING = "EXISTS (SELECT 1 FROM cms_complaints c WHERE c.id = complaint_id)"


def upgrade() -> None:
    for stmt in org_policy_sql("cms_complaints") + org_policy_sql(
        "cms_complaint_events", using=EVENTS_USING
    ):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in drop_org_policy_sql("cms_complaint_events") + drop_org_policy_sql("cms_complaints"):
        op.execute(stmt)
