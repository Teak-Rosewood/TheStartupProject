import pytest

pytest.importorskip("sqladmin")
from tsp.trends.admin import TREND_VIEWS  # noqa: E402


def test_views_keep_the_identity_staff_roles_grant_on():
    # sqladmin would otherwise name them after the model class ("topicrow", ...): not what roles grant on.
    assert [v.identity for v in TREND_VIEWS] == ["trend-topic", "trend-signal", "trend-candidate"]
    assert len({v.identity for v in TREND_VIEWS}) == len(TREND_VIEWS)
