# Reader engagement (`tsp.engage`)

Position polls before and after reading, proof of reading, weekly streaks, points and badges, onboarding, and structured takes (pick a side, write a few sentences) with moderation and appeals. Package: `python/packages/engage`. Example domain: recipe reviews ("would you cook this again?").

## Reading receipts
The server calls `Receipts.start(user, org, item, version, expected_seconds=)` when it sends the **full** item (a paywalled preview does not count); the page then sends beacons to `POST /api/engage/receipts/{item}/ping` with dwell time, scroll depth and whether the end was reached. Progress only moves forward, and dwell is capped by the real time since the item was served, so a client cannot claim minutes it did not spend. An item counts as read at `ReadPolicy(min_dwell_ratio=0.4, require_end=True)`. A new version of the item starts reading over; reopening the same version keeps progress. Pings are rate limited through the `allow_ping` callback.

The first time an item counts as read, `Engage.record_read` pays points, advances the streak and may grant badges. Repeat calls do nothing.

## Polls
`Polls.create(org, item, "scale1d" | "scale2d", axes)` where `axes = {"x": {"label", "min", "max", ...}, "y": {...}}`. A reader answers once before and once after reading; answers are final and the 'after' needs the 'before' unless the poll sets `before_required: false`. `shift(user, poll)` gives the reader's own movement. `aggregate(poll)` returns histograms and the mean shift, but **withholds anything based on fewer than `min_k` (default 20) people**, so a small crowd cannot be read person by person. A reader's answers are returned only to that reader (`GET /api/engage/me`).

## Streaks, points, badges, onboarding
- **Streaks:** a week (Monday start, in `EngageConfig.timezone`) counts when the reader finished `weekly_goal` items (default 1). Missing a whole week resets `current` to 0 and keeps `best`. The streak still shows as alive during the week after the last good one.
- **Points:** a log paid once per (reason, thing); amounts in `EngageConfig.points`. Badges (`BadgeRule`) are granted by count of a reason or by streak length.
- **Onboarding:** `required_onboarding` steps per organization; `Onboarding.is_complete` gates submitting.

## Takes (submissions)
One take per (item, reader, side): a side (free text such as `for` / `against`) and a body. **No replies, threads or mentions exist in the data model.** A rejected or pending take can be edited (and is moderated again); an accepted or appealed one is locked.

`can_submit(user, org, item)` returns every unmet precondition by name: `not_signed_in`, `not_subscribed`, `onboarding_incomplete`, `not_read`. The subscription check is a callback you provide (`Submissions(entitled=...)`); with `require_subscription=True` (the default) and no callback, nobody can submit.

**Moderation** is the `tsp.pipeline` workflow `engage.moderate` (`build_workflow(moderators)`): one step per moderator, in order, then a decision step. Each moderator returns pass, fail or flag with a reason written for the author.
- `Length`, `Blocklist` (whole words in any script; the reason does not repeat the word), `LlmRubric(dimension)` (a 0–1 score from a registered prompt: `engage.civility`, `engage.relevance`, `engage.substance`; below `fail_below` it fails, below `flag_below` a person looks), and `FactCheckHook(fn)` for your own check.
- Cheap checks run first; once one fails, later steps (and their model calls) are skipped.
- Any fail rejects, with the reasons shown to the author. A flag leaves the take pending for staff (`/engage/staff/review-queue`, then accept, or reject with a reason). Otherwise it is accepted and paid `submission_accepted`, plus `submission_evidence` when the substance score reaches `evidence_threshold`.
- The decision step skips a take that was edited or decided since the run began, so an old run can neither reject nor pay a newer text.
- Push the bundled prompts with `push_prompts(session)` (the sample app does at start). The prompts are starting points: tune them with `tsp-pipeline eval` and your own examples.

**Appeals:** `POST /api/engage/submissions/{id}/appeal` opens a complaint in the complaints desk (category `moderation_appeal`, which the desk's categories must include; the sample app adds it). One appeal per take. A reviewer decides at `/engage/staff/submissions/{id}/appeal` with `upheld` true (the take is accepted and paid) or false (it stays rejected); the complaint is closed with the same text.

**Reading:** `GET /api/engage/public/items/{item}/submissions?org_id=&side=&limit=&offset=` returns accepted takes only, newest first, with the display name and points and nothing else about the author.

## In the sample app
Polls, receipts, streaks, points, badges and onboarding are always mounted (`/api/engage/...`, `org_id` names the publishing organization). Takes and moderation need an AI provider key (the workflow calls a model). Settings: `ENGAGE_TIMEZONE`, `ENGAGE_WEEKLY_GOAL`, `ENGAGE_ONBOARDING_STEPS`, `ENGAGE_REQUIRE_SUBSCRIPTION` (`0` lets any onboarded reader submit), `ENGAGE_MODERATION_MODEL`, `ENGAGE_PRICE_IN` / `ENGAGE_PRICE_OUT` (USD per million tokens; the defaults are placeholders to replace), `ENGAGE_BLOCKLIST` (comma separated). A reader counts as a subscriber when an organization they belong to has an active paid plan. Add `tsp.pipeline` and `tsp.engage` to `TSP_MIGRATE_MODULES` (the Dockerfile does).

## Privacy
Polls, reads, streaks, points, badges, onboarding and takes are personal data: they are exported and erased with the account (`register_with_compliance`). Moderation sends the take's text to your AI provider; list it in [sub-processors](../legal/subprocessors.md). Rejected takes and their reasons stay with the author only.

Rollback: `engage_0002` only adds `en_submissions`; downgrading drops it (and the takes) without touching the K6-A tables.

## Not built
Creating polls over HTTP (use `Polls.create` from your editor tools), admin views for polls and takes, a points shop, and notifications about moderation results.
