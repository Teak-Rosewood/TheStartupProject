# K6 Engagement (`tsp.engage`)

**Goal:** position polls on a 1D or 2D scale (before and after reading), private history, read receipts, weekly streaks, points and badges, onboarding completion as a precondition, and structured submissions (pick a side, write a take) with a pluggable moderation pipeline, no replies, and appeals into the K4 complaints desk.

**Depends on:** K3 (moderation steps use `tsp.pipeline`/`tsp.ai`), K4 (appeals), `tsp.accounts`. **Branch:** `feat/k6-<step>`. **Size:** medium; two pull requests.

## K6-A Polls, receipts, streaks, points
Tables:
| Table | Columns |
|---|---|
| `en_polls` | id, org_id, item_id, kind (`scale1d`/`scale2d`), axes jsonb (labels, min, max), opens_at, closes_at |
| `en_responses` | poll_id, user_id, phase (`before`/`after`), x, y nullable, at; pk (poll_id, user_id, phase) |
| `en_receipts` | user_id, item_id, version_id, started_at, completed_at, dwell_seconds, max_scroll, reached_end bool, served_full bool; pk (user_id, item_id) |
| `en_streaks` | user_id, org_id, period (`week`), current, best, last_period_key (e.g. `2026-W41`) |
| `en_points` | id, user_id, org_id, reason, points, ref_kind, ref_id, at; `en_badges` (user_id, badge, at) |
| `en_onboarding` | user_id, org_id, step, completed_at; required steps configured per org |

Interfaces:
```python
Receipts.start(user, item, version)          # server-side when the full item is served
Receipts.ping(user, item, dwell, scroll, reached_end)   # client beacons, rate limited
Receipts.is_read(user, item, policy=ReadPolicy(min_dwell_ratio=0.4, require_end=True, expected_seconds=...)) -> bool
Polls.respond(user, poll, phase, x, y=None)  # `after` only allowed once `before` exists or poll config says optional
Polls.shift(user, poll) -> (dx, dy); Polls.aggregate(poll) -> histogram (k-anonymous, min 20 responses)
Streaks.on_read(user, at)   # week keys in the org's timezone; goal N per week configurable (default 1)
Points.award(user, reason, points, ref); rules in config: {reason: points}
Onboarding.complete(user, step); Onboarding.is_complete(user) -> bool
```
Private history: `GET /api/engage/me` returns the user's own responses, shifts, streak and points; never exposed to others.

## K6-B Submissions and moderation
Tables: `en_submissions` (id, org_id, item_id, user_id, side (free text, e.g. `for`/`against`), body, status (`pending`/`accepted`/`rejected`/`appealed`), reasons jsonb, scores jsonb, points_awarded, created_at, decided_at); one submission per (item, user, side) editable until accepted.

Preconditions (`can_submit(user, item)`): signed in; subscriber (pluggable entitlement check); `Onboarding.is_complete`; `Receipts.is_read`. Each failure returns a machine-readable reason.

No replies by design: no parent id, no threading, no mentions.

Moderation pipeline: ordered `Moderator` plug-ins, each `async def run(sub, item) -> Verdict(pass|fail|flag, reason, score)`. Built-ins: `length`, `blocklist`, `llm_rubric(rubric_key, model)` using a K3 prompt (one per dimension, e.g. civility, relevance, substance) and `fact_check_hook` (calls an app-supplied function). Runs as a `tsp.pipeline` workflow `engage.moderate`; any fail rejects with reasons shown to the author; any flag sends to human review in admin.

Points: configurable scoring from moderator scores (e.g. evidence-backed bonus).

Appeals: `POST /api/engage/submissions/{id}/appeal` opens a K4 complaint with category `moderation_appeal`; resolving it can flip the status.

Public read API: accepted submissions per item and side, paginated, with author display name and points.

## Tests
- Uncivil sample rejected with a reason; on-topic, evidenced sample accepted with points.
- Cannot submit without receipt; cannot submit before onboarding complete.
- Streak counts across a week boundary and timezone edge; misses reset `current` but keep `best`.
- Poll shift computed; aggregate hidden below 20 responses.
- Appeal creates a complaint.

## Done when
An uncivil sample submission is rejected with a reason; streaks count correctly across week boundaries; `docs/modules/engage.md` written.

## Prompt
```
Repository: Teak-Rosewood/TheStartupProject. Branch: feat/k6-X (X = A then B).
Read docs/plans/K6-engage.md section K6-X, docs/plans/README.md, tsp.pipeline, tsp.cms
(complaints) and tsp.accounts. Implement the section. Example domain: recipe reviews
("would you cook this again": for/against). Recorded LLM fixtures. Run checks; open a PR.
```
