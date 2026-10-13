import os
from datetime import datetime, timezone

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
TABLES = "en_submissions, en_onboarding, en_badges, en_points, en_streaks, en_receipts, en_responses, en_polls, jobs, cron_jobs, cms_complaint_events, cms_complaints, alembic_version"
ORG = "pub1"


class Clock:
    def __init__(self, at=datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc)):  # a Wednesday
        self.at = at

    def __call__(self):
        return self.at
