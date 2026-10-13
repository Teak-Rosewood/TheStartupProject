import pytest

pytest.importorskip("sqladmin")
from tsp.sources.admin import SOURCE_VIEWS, CheckView, RunView, SourceView  # noqa: E402


def test_views_keep_the_identity_staff_roles_grant_on():
    # sqladmin would otherwise name them after the model class ("run", "check"): too generic to grant on.
    assert [v.identity for v in SOURCE_VIEWS] == ["source", "source-run", "source-check"]
    assert (SourceView.identity, RunView.identity, CheckView.identity) == (
        "source",
        "source-run",
        "source-check",
    )
    assert len({v.identity for v in SOURCE_VIEWS}) == len(SOURCE_VIEWS)
