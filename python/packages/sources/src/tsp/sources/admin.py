"""Read-only sqladmin views. Needs the ``admin`` extra. Add them to your admin app with
``admin.add_view(SourceView)`` and so on; source rows are changed with ``tsp-sources import``."""

from __future__ import annotations

from sqladmin import ModelView

from .models import Check, Run, Source


class _ReadOnly(ModelView):
    can_create = can_edit = can_delete = can_export = False
    page_size = 50


class SourceView(_ReadOnly, model=Source):
    name, name_plural, identity, icon = "Source", "Sources", "source", "fa-solid fa-database"
    column_list = [
        Source.key,
        Source.name,
        Source.tier,
        Source.enabled,
        Source.robots_ok,
        Source.schedule,
    ]
    column_searchable_list = [Source.key, Source.name]


class RunView(_ReadOnly, model=Run):
    name, name_plural, identity, icon = (
        "Source run",
        "Source runs",
        "source-run",
        "fa-solid fa-clock",
    )
    column_list = [
        Run.id,
        Run.source_id,
        Run.stream,
        Run.strategy_used,
        Run.status,
        Run.started_at,
        Run.items_new,
        Run.errors,
        Run.cost,
    ]
    column_default_sort = [(Run.started_at, True)]


class CheckView(_ReadOnly, model=Check):
    name, name_plural, identity, icon = (
        "Source check",
        "Source checks",
        "source-check",
        "fa-solid fa-heart-pulse",
    )
    column_list = [
        Check.id,
        Check.source_id,
        Check.checked_at,
        Check.reachable,
        Check.robots_ok,
        Check.notes,
    ]
    column_default_sort = [(Check.checked_at, True)]
