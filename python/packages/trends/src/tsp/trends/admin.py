"""Read-only sqladmin views (extra ``admin``): add with ``admin.add_view(CandidateView)`` and so on."""

from __future__ import annotations

from sqladmin import ModelView

from .models import CandidateRow, SignalRow, TopicRow


class _ReadOnly(ModelView):
    can_create = can_edit = can_delete = can_export = False
    page_size = 50


class TopicView(_ReadOnly, model=TopicRow):
    name, name_plural, identity, icon = "Topic", "Topics", "trend-topic", "fa-solid fa-hashtag"
    column_list = [TopicRow.org_id, TopicRow.key, TopicRow.label, TopicRow.active]


class SignalView(_ReadOnly, model=SignalRow):
    name, name_plural, identity, icon = (
        "Signal",
        "Signals",
        "trend-signal",
        "fa-solid fa-chart-line",
    )
    column_list = [
        SignalRow.org_id,
        SignalRow.signal,
        SignalRow.topic_id,
        SignalRow.at,
        SignalRow.value,
    ]
    column_default_sort = [(SignalRow.at, True)]


class CandidateView(_ReadOnly, model=CandidateRow):
    name, name_plural, identity, icon = (
        "Trend candidate",
        "Trend candidates",
        "trend-candidate",
        "fa-solid fa-fire",
    )
    column_list = [
        CandidateRow.day,
        CandidateRow.org_id,
        CandidateRow.label,
        CandidateRow.score,
        CandidateRow.status,
    ]
    column_default_sort = [(CandidateRow.day, True), (CandidateRow.score, True)]


TREND_VIEWS = [TopicView, SignalView, CandidateView]

# sqladmin derives ``identity`` from the model class name when the class is created and ignores one
# set in the class body, so set it afterwards. The identity is also what the staff roles grant on.
for _view, _identity in zip(
    TREND_VIEWS, ("trend-topic", "trend-signal", "trend-candidate"), strict=True
):
    _view.identity = _identity
