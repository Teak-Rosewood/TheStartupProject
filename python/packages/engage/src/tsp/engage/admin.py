"""Read-only sqladmin views (extra ``admin``): pass ``ENGAGE_VIEWS`` to ``mount_admin(extra_views=...)``."""

from __future__ import annotations

from sqladmin import ModelView

from .models import Poll, Submission


class _ReadOnly(ModelView):
    can_create = can_edit = can_delete = can_export = False
    page_size = 50


class PollView(_ReadOnly, model=Poll):
    name, name_plural, icon = "Poll", "Polls", "fa-solid fa-square-poll-vertical"
    column_list = [Poll.org_id, Poll.item_id, Poll.kind, Poll.opens_at, Poll.closes_at]
    column_searchable_list = [Poll.item_id]


class SubmissionView(_ReadOnly, model=Submission):
    name, name_plural, icon = "Take", "Takes", "fa-solid fa-comments"
    column_list = [
        Submission.created_at,
        Submission.org_id,
        Submission.item_id,
        Submission.side,
        Submission.status,
        Submission.needs_review,
        Submission.author_name,
    ]
    column_searchable_list = [Submission.item_id]
    column_default_sort = [(Submission.created_at, True)]


ENGAGE_VIEWS = [PollView, SubmissionView]

# sqladmin derives ``identity`` from the model class name and ignores one set in the class body.
for _view, _identity in zip(ENGAGE_VIEWS, ("engage-poll", "engage-take"), strict=True):
    _view.identity = _identity
