# `tsp.jobs` and `tsp.notify`

## `tsp.jobs` — background work on Postgres
No queue service to run or pay for: jobs are rows.

```python
registry = JobRegistry()

@registry.job("send_report")
async def send_report(ctx, payload):          # ctx.session commits only if this returns normally
    ...

registry.schedule("nightly-report", "daily 02:30", "send_report", {"kind": "usage"})   # UTC

await enqueue(session, "send_report", {"org": org_id}, unique_key=f"report:{org_id}:2026-10")
```
- **Transactional enqueue.** `enqueue` writes in *your* transaction: if the request rolls back, the job never existed; if it commits, the job is guaranteed to run. This is the outbox pattern for free.
- **At-least-once.** Workers claim jobs with `FOR UPDATE SKIP LOCKED`, so any number can run side by side and each job runs on exactly one at a time (the test runs 4 workers over 40 jobs: every job once). A worker that dies loses its lease after 10 minutes and the job is taken again, so write handlers to survive a repeat (use `unique_key`, idempotency refs).
- **Retries.** Failures re-queue with exponential backoff (10 s doubling, capped at an hour, jittered) up to `max_attempts` (default 5), then the job is `dead` and keeps its error for you to read in the database. A job with no registered handler goes straight to `dead`.
- **Schedules** (`every 15m`, `every 2h`, `daily 02:30`) are rows in `cron_jobs`; due schedules become jobs with a unique key per tick, so two workers racing create one.
- **Where it runs.** At stage 0 inside the API process (`create_app(background=[worker_lifespan(registry)])`, no spare memory for another container). For scale-out set `TSP_JOBS_INLINE=0` and run `tsp-jobs worker --registry tsp_api.notifications:...` as a second ECS service from the same image.

Jobs this repo ships (`python/apps/api/src/tsp_api/notifications.py`): send email, poll SES events, run due account deletions (hourly), issue missing invoices (every 15 min), housekeeping (daily: finished jobs after 14 days, webhook payloads after 90, email outbox after 400).

## `tsp.notify` — email, push, SMS, in-app
### Email
- **Outbox.** `notifier.queue(session, to, "invite", ctx)` renders once (so a bad template fails now, not at 3 a.m.), stores the message and enqueues delivery in your transaction. Delivery is idempotent: a retried job never sends twice.
- **Templates.** Jinja, HTML plus an automatic plain-text part; values are HTML-escaped; a missing variable is an error. Ships: `welcome`, `invite`, `receipt`, `payment_failed`, `deletion_scheduled`, `marketing_update`. Add your own in a directory passed to `Templates(...)`.
- **Transactional vs marketing.** Marketing is **opt-in** (no preference row = no consent), uses its own SES configuration set, and carries `List-Unsubscribe` plus one-click `List-Unsubscribe-Post` headers and a footer link. The link opens a confirmation page (mail scanners follow links, so a GET must not unsubscribe anyone); the button, or a mail client's one-click POST, does. Unsubscribing never blocks receipts or security mail.
- **Suppression.** Permanent bounces and complaints are suppressed for all mail, forever (even after account deletion, deliberately). Transient bounces are left to SES.
- **Events.** SES publishes bounces and complaints to SNS → SQS; the `notify.poll_ses_events` job reads the queue every minute. There is no public webhook to protect. Unparseable messages stay on the queue and move to the dead-letter queue.
- **Sent from the app:** welcome on first sign-in, invitation with a one-time link, receipt with each invoice, payment-failed notice, and deletion-scheduled confirmation. Hooks are best effort in a savepoint: an email problem never fails the action that caused it.

### Push, SMS, in-app
- **Push:** FCM HTTP v1 (`FcmPush`) from a service-account JSON; one OAuth token is reused, dead tokens are returned for deletion. Device tokens: `PUT /me/devices`.
- **SMS:** `SnsSms` via Amazon SNS. In India every transactional SMS needs a **DLT-registered sender id and content template**; registration takes days and the template ids go in as message attributes. Budget for per-message cost and prefer email, push or passkeys for sign-in codes.
- **In-app:** `notify_user(...)`, `GET /me/notifications`, `POST /me/notifications/read`.

### Setup
See [aws/15-email.md](../aws/15-email.md) (DNS records, SES production access, DMARC).

### Data protection
Notification data joins the export and erasure flows: erasing a user deletes their notifications and device tokens and replaces recipient addresses in the outbox with a hash and empties the stored context. Bounce and complaint records stay.
