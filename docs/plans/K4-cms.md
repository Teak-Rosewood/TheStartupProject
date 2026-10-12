# K4 Editorial backend (`tsp.cms`)

**Goal:** content items with versions and language variants, review states, pluggable publish checks, scheduled publish, a public corrections and updates log, a review cadence (living or frozen, with auto-freeze), a complaints desk with deadlines and escalation, disclosure blocks, and restricted-mode windows. Generic: examples are recipes and product guides.

**Depends on:** K3, `tsp.flags`, `tsp.audit`, `tsp.notify`. **Branch:** `feat/k4-<step>`. **Size:** large; three pull requests.

## K4-A Items, versions, publishing
Tables:
| Table | Columns |
|---|---|
| `cms_items` | id, org_id, type (free text), slug, state (`draft`/`in_review`/`approved`/`scheduled`/`published`/`unpublished`), current_version_id, published_version_id, publish_at, lifecycle (`open`/`living`/`frozen`), review_due_at, frozen_at, frozen_reason, created_by, attrs |
| `cms_versions` | id, item_id, number, lang, title, body jsonb (blocks), summary, disclosures jsonb, author_kind (`human`/`ai`/`mixed`), ai_run_id (pl_runs), created_by, created_at, change_note |
| `cms_variants` | item_id, lang, version_id, state; pk (item_id, lang) |
| `cms_check_results` | id, version_id, check, passed, details jsonb, ran_at |

Interfaces:
```python
class PublishCheck(Protocol):
    name: str; blocking: bool
    async def run(self, item, version) -> CheckResult
register_check(item_type, check)

CmsService: create, save_version(item_id, lang, body, author_kind, ai_run_id=None, change_note=""),
            submit(item_id), approve(item_id, reviewer), schedule(item_id, at), publish(item_id, actor),
            unpublish(item_id, reason), diff(v1, v2)
```
Built-in checks: `required_fields`, `disclosure_present` (fails if `author_kind != human` and no AI disclosure block), `all_variants_ready` (configurable languages). Publishing runs every registered check; a blocking failure stops it. Job `cms.publish_due` every minute publishes scheduled items. Every transition writes `tsp.audit`.

Disclosure blocks: typed blocks (`ai_assistance`, `methodology`, `conflict`) with templates per org and language.

## K4-B Corrections, updates, lifecycle
Tables: `cms_corrections` (id, item_id, version_from, version_to, kind (`correction`/`clarification`/`update`), public_note, internal_note, complaint_id nullable, created_by, created_at); `cms_reviews` (item_id, due_at, decided_at, decision (`living`/`frozen`), decided_by).

Rules:
- Publishing a new version of a published item requires a `kind` and `public_note`; it appends to the corrections log.
- Public API `GET /public/cms/items/{slug}/corrections` and an org-wide `GET /public/cms/corrections` feed (RSS too).
- Review cadence per item type in config (`review_after_days=30`, `auto_freeze_after_days=7`): on publish set `review_due_at`; job `cms.lifecycle` daily: due items notify editors; after the grace period with no decision set `lifecycle=frozen`, `frozen_reason="auto"`, add a public update note from a template ("This article is no longer updated as of <date>"). `living` items get a new `review_due_at` every cycle.
- `is_refreshable(item)` helper that jobs in apps call to skip frozen items.

## K4-C Complaints desk and restricted windows
Tables: `cms_complaints` (id, org_id, item_id nullable, complainant_name, email, category, body, received_at, ack_due_at, resolve_due_at, status (`received`/`acknowledged`/`in_progress`/`resolved`/`rejected`/`escalated`), level (1..3), assigned_to, resolution, correction_id, closed_at); `cms_complaint_events` (complaint_id, at, actor, kind, note).

Rules:
- Deadlines are config per org (`ack_hours`, `resolve_days`, escalation levels and who handles each). Defaults neutral; the app sets the legal values.
- Intake: `POST /public/cms/complaints` (rate limited, captcha hook), sends acknowledgement through `tsp.notify` with a reference number.
- Job `cms.complaints_sla` hourly: warn before deadlines, auto-escalate on breach.
- Monthly report `GET /api/cms/complaints/report?month=` (counts by category, status, median times) and a CSV export.
- Resolving with a correction links `cms_corrections.complaint_id`.

Restricted-mode windows: `cms_windows` (id, org_id, name, starts_at, ends_at, region, rules jsonb: e.g. `{"block_types": ["poll_results"], "require_check": ["window_review"]}`); `active_windows(at, region)`; a publish check `window_rules` that blocks or flags items during a window; windows also set a `tsp.flags` flag `cms.window.<name>` so other modules can react.

Admin views for all tables; editor API under `/api/cms/...` with org roles `editor`, `reviewer`.

## Tests
Draft → published with checks; AI version without disclosure blocked; scheduled publish; correction required on republish; auto-freeze after grace; complaint SLA escalation with a frozen clock; window blocks a type during its dates only; compliance exporter and eraser for complainant data.

## Done when
An item goes draft → published with checks; a complaint opens a correction; an item auto-freezes when no review decision is made; `docs/modules/cms.md` written.

## Prompt
```
Repository: Teak-Rosewood/TheStartupProject. Branch: feat/k4-X (X = A, B, C in order).
Read docs/plans/K4-cms.md section K4-X and docs/plans/README.md; read tsp.flags, tsp.audit,
tsp.notify, tsp.jobs and tsp.pipeline. Implement the section with neutral examples
(recipes, product guides). Use a frozen clock in tests. Run checks; open a PR.
```
