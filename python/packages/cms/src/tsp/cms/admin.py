"""Read-only sqladmin views (extra ``admin``): pass ``CMS_VIEWS`` to ``mount_admin(extra_views=...)``."""

from __future__ import annotations

from sqladmin import ModelView

from .models import Complaint, ComplaintEvent, Correction, Item, Review, Window


class _ReadOnly(ModelView):
    can_create = can_edit = can_delete = can_export = False
    page_size = 50


class CmsItemView(_ReadOnly, model=Item):
    name, name_plural, identity, icon = (
        "Content item",
        "Content items",
        "cms-item",
        "fa-solid fa-newspaper",
    )
    column_list = [
        Item.org_id,
        Item.type,
        Item.slug,
        Item.state,
        Item.lifecycle,
        Item.review_due_at,
        Item.published_at,
    ]
    column_searchable_list = [Item.slug]
    column_default_sort = [(Item.published_at, True)]


class CorrectionView(_ReadOnly, model=Correction):
    name, name_plural, identity, icon = (
        "Correction",
        "Corrections",
        "cms-correction",
        "fa-solid fa-pen-to-square",
    )
    column_list = [
        Correction.created_at,
        Correction.org_id,
        Correction.item_id,
        Correction.kind,
        Correction.public_note,
        Correction.complaint_id,
    ]
    column_default_sort = [(Correction.created_at, True)]


class ReviewView(_ReadOnly, model=Review):
    name, name_plural, identity, icon = (
        "Review",
        "Reviews",
        "cms-review",
        "fa-solid fa-clipboard-check",
    )
    column_list = [
        Review.item_id,
        Review.due_at,
        Review.notified_at,
        Review.decided_at,
        Review.decision,
        Review.decided_by,
    ]
    column_default_sort = [(Review.due_at, True)]


class ComplaintView(_ReadOnly, model=Complaint):
    name, name_plural, identity, icon = (
        "Complaint",
        "Complaints",
        "cms-complaint",
        "fa-solid fa-triangle-exclamation",
    )
    column_list = [
        Complaint.received_at,
        Complaint.org_id,
        Complaint.category,
        Complaint.status,
        Complaint.level,
        Complaint.assigned_to,
        Complaint.ack_due_at,
        Complaint.resolve_due_at,
    ]
    column_default_sort = [(Complaint.received_at, True)]


class ComplaintEventView(_ReadOnly, model=ComplaintEvent):
    name, name_plural, identity, icon = (
        "Complaint event",
        "Complaint events",
        "cms-complaint-event",
        "fa-solid fa-timeline",
    )
    column_list = [
        ComplaintEvent.at,
        ComplaintEvent.complaint_id,
        ComplaintEvent.actor,
        ComplaintEvent.kind,
    ]
    column_default_sort = [(ComplaintEvent.at, True)]


class WindowView(_ReadOnly, model=Window):
    name, name_plural, identity, icon = (
        "Restricted window",
        "Restricted windows",
        "cms-window",
        "fa-solid fa-lock",
    )
    column_list = [Window.org_id, Window.name, Window.region, Window.starts_at, Window.ends_at]
    column_default_sort = [(Window.starts_at, True)]


CMS_VIEWS = [CmsItemView, CorrectionView, ReviewView, ComplaintView, ComplaintEventView, WindowView]

# sqladmin derives ``identity`` from the model class name when the class is created and ignores one
# set in the class body, so set it afterwards. The identity is also what the staff roles grant on.
for _view, _identity in zip(
    CMS_VIEWS,
    (
        "cms-item",
        "cms-correction",
        "cms-review",
        "cms-complaint",
        "cms-complaint-event",
        "cms-window",
    ),
    strict=True,
):
    _view.identity = _identity
