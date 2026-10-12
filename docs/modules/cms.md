# Editorial backend (`tsp.cms`)

Content items with versions, language variants, a review workflow, pluggable publish checks, scheduled publishing and disclosure blocks. Examples use recipes and product guides; the package is not tied to one kind of content.

Install `tsp-cms`, add `tsp.cms` to `TSP_MIGRATE_MODULES` (it needs `tsp.audit` and `tsp.jobs`), and build `CmsService(session)` per request. Register the scheduler job with `register_jobs(job_registry)`.

## Items, versions, variants

An item (`type`, `slug` unique per organization, `primary_lang`) has numbered versions. A version is never edited: `save_version(org, item_id, lang, title=, body=, ...)` stores a new one and makes it the latest in that language. `body` is a list of blocks (`{"type": "paragraph", "text": ...}`; the shape is yours). A version records `author_kind` (`human`, `ai`, `mixed`), the `tsp.pipeline` run that produced it (`ai_run_id`) and a change note. `cms_variants` keeps the latest version per language and whether it is `draft`, `ready` (`mark_variant_ready`) or `published`. `diff(org, a, b)` compares titles and blocks.

## Workflow

`draft` -> `submit` -> `in_review` -> `approve` -> `approved` -> `publish`, or `schedule(at)` and let the job publish. `request_changes` returns an item to `draft`. Rules the service enforces:

- Saving a new version of an item that is in review, approved or scheduled sends it back to `draft`, so what is published is what a reviewer approved.
- A version cannot be approved by the person who wrote it (`allow_self_approval=True` turns this off for one-person teams).
- A published item keeps serving its published version while a newer draft is prepared; the newer draft goes through submit, approve and publish again. `get_published(org, slug, lang)` is what readers see.
- `unpublish` needs a reason and removes the item from `get_published`.
- Every transition writes an audit entry (`cms.item.submit`, `.approve`, `.publish`, `.unpublish`, `.publish_blocked`...).

## Publish checks

Publishing runs every check registered for the item's type (`"*"` applies to all) against the current version and stores each result in `cms_check_results`. A failed `blocking` check raises `PublishBlocked` (with the failed checks and their details) and leaves the item as it was; a failed non-blocking check is recorded as a warning in the audit entry. Built in and registered by `default_registry()`: `required_fields` (slug, title, body) and `disclosure_present` (content with `author_kind` other than `human` needs an `ai_assistance` disclosure block). `AllVariantsReady(["en", "hi"])` is available when an item type must ship in several languages at once. Add your own: a class with `name`, `blocking` and `async run(ctx, item, version) -> CheckResult`, then `registry.register("recipe", MyCheck())` and pass `CmsService(session, checks=registry)`.

## Disclosure blocks

Blocks of type `ai_assistance`, `methodology` or `conflict` in `version.disclosures`. `disclosure(org, type, lang)` returns a block with the organization's wording (`set_disclosure_template`), falling back from `hi-IN` to `hi` to `en` to built-in English text.

## Scheduling

`schedule(org, item_id, at, actor)` needs an approved item and a timezone-aware time. The `cms.publish_due` job runs every minute and publishes due items. An item that fails its checks at that moment goes back to `approved` with a `cms.item.publish_blocked` audit entry, so it is not retried every minute. Tests drive the clock with `CmsService(session, clock=...)`.

## Editor API

`build_router(principal, session)` serves `/cms/items` (create, read, versions, submit, approve, request-changes, schedule, publish, unpublish). `principal` is your dependency returning `Principal(org_id, sub, roles)`. Role `editor` can create, edit and submit; role `reviewer` can approve, request changes, schedule, publish and unpublish. A blocked publish returns 409 with the failed checks.

## Corrections and updates log

Replacing a published version needs a `kind` (`correction`, `clarification` or `update`) and a `public_note`; `publish(..., kind=, public_note=, internal_note=)` refuses without them and leaves the old version live. A scheduled republish takes the same fields at `schedule` time and applies them when the job publishes. `add_note` adds a log entry without a new version (a clarification, or the outcome of a complaint). The internal note stays in the CMS.

`build_public_router(session, org, base_url=)` serves the log without sign-in, cached for five minutes: `GET /public/cms/corrections` (all items), `GET /public/cms/items/{slug}/corrections`, and the same as RSS at `…/corrections.rss`. `org` is a fixed organization id or a function of the request (for example the Host header). Only entries of published items appear.

## Review cadence and freezing

Publishing sets `review_due_at` to `review_after_days` later (default 30; override per item type with `LifecycleConfig(by_type={"news": (7, 3)})`). The daily `cms.lifecycle` job opens a review for items that are due and calls your `notify_review_due(item, deadline)` hook once (wire it to `tsp.notify`; it is not wired in the sample app). If nobody decides within `auto_freeze_after_days` (default 7) the item is frozen: `lifecycle="frozen"`, `frozen_reason="auto"`, and a public update note ("This article is no longer updated as of 9 April 2026.", in the item's language if a translation is configured). The job is safe to run twice.

Editors decide with `decide_review(org, item_id, "living" | "frozen", actor, reason)`: `living` starts a new cycle (living items freeze too if a later review goes undecided), `frozen` freezes at once. `unfreeze` (reason required) returns a frozen item to `living`. Corrections can still be published to a frozen item and do not restart reviews. Jobs that rewrite, translate or re-fetch published content should skip anything for which `is_refreshable(item)` is false.

The editor API gains `POST /cms/items/{id}/review`, `/unfreeze` and `/notes`, and `publish` and `schedule` accept the correction fields.

Rollback: migration `cms_0002` only adds the `cms_corrections` and `cms_reviews` tables; downgrading drops them (the log is lost, content is not).

## Not built yet

The complaints desk with deadlines and escalation and restricted-mode windows (K4-C), admin views, wiring the review reminder and the public feed into the sample app, and compliance export/erasure for complainant data.
