# 03 · Metrics and analytics

> Guidance for founders, not legal, tax or financial advice. Rules (especially Indian tax, data-protection and payment rules) change; items marked **(verify)** need a check with a chartered accountant (CA), company secretary (CS) or lawyer before you act.

Measure few things well. Decide the questions first, then the events.

## One north-star metric
The count of **successful outcomes your users want per week**, not signups. Examples: documents exported, reports generated, orders fulfilled, AI tasks completed.

## Funnel (AARRR)
| Stage | Question | Typical event |
|---|---|---|
| Acquisition | where do visitors come from? | `landing_viewed` with UTM and referrer |
| Activation | did they reach the first value? | `signup_completed`, `first_<outcome>_completed` |
| Retention | do they come back? | active in week N after signup |
| Revenue | do they pay? | `checkout_started`, `payment_succeeded`, `subscription_cancelled` |
| Referral | do they bring others? | `invite_sent`, `referral_signed_up` |

## Event naming
`object_action`, lowercase snake_case, past tense: `project_created`, `invite_accepted`. Every event carries: `event`, `timestamp` (UTC), `anonymous_id`, `user_id` (when known), `org_id` (when known), `properties`. **No email addresses, names, free-text user content or payment details in properties.**

## What this kit gives you
- `landing_viewed`, `pricing_viewed`, `blog_viewed`, `legal_viewed`, `cta_clicked`, `waitlist_submitted` are sent by the landing site (after consent).
- Add `signup_completed`, `first_<outcome>_completed`, `checkout_started` and `payment_succeeded` from your app; the funnel in the staff console's *Metrics* page uses whatever names you define.
- First-touch UTM parameters and referrer are stored on the user at sign-up, so "which channel brings paying customers" is a query, not a guess.

## SaaS definitions (agree these once)
| Metric | Definition |
|---|---|
| MRR | sum of monthly-equivalent recurring revenue from active subscriptions, excluding tax |
| ARR | MRR × 12 |
| Logo churn | customers lost in a month ÷ customers at the start of the month |
| Net revenue retention | (start MRR + expansion − contraction − churn) ÷ start MRR, for the same cohort |
| CAC payback | acquisition spend ÷ (new MRR × gross margin), in months |
| ARPU | MRR ÷ active paying accounts |

## AI products
Track **cost per active user** and **gross margin per request** from day one: tokens in and out, provider, model, latency, and the price you charged. A feature that costs more in model fees than it earns will not survive growth. Set per-user and per-organization limits before launch.

## Privacy
Ask for consent before non-essential analytics (see the [Cookie Policy](../legal/cookie-policy.md)). Prefer first-party event storage or a privacy-focused tool, and list any analytics vendor in the [sub-processors](../legal/subprocessors.md).
