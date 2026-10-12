# 06 · Support and operations

> Guidance for founders, not legal, tax or financial advice. Rules (especially Indian tax, data-protection and payment rules) change; items marked **(verify)** need a check with a chartered accountant (CA), company secretary (CS) or lawyer before you act.

## Support
- One public address (`support@`) that goes to a shared inbox, not one person. Start with a free helpdesk tier (Zoho Desk, Freshdesk) or a shared mailbox with labels.
- Publish response targets and keep them: for a small team, "first reply within one business day" is realistic.
- Link to a help centre or FAQ from the app. The best support is the question that never arrives: log what people ask and fix the product.
- Add an in-app feedback button that records the page, version and user id; route it to the same inbox.
- Reply to security reports (`security@`) within 3 business days ([SECURITY.md](../../SECURITY.md)).

## Status page and uptime
- Use a hosted status page (Better Stack, Instatus, UptimeRobot free tiers) on a separate domain or provider from your production, so it stays up when you do not. Monitor `https://<domain>/api/health` and the login page.
- When something breaks: acknowledge within 15 minutes, update every 30–60 minutes, close with a short explanation.

## On-call for a small team
- Name a primary and a backup for every week, even with two people. Alarms go to email plus a phone push (the SNS topic can notify a pager service).
- Keep a short [runbook](../aws/12-runbooks.md) for each alarm. If an alarm does not need a human, delete it.
- After each incident, write a blameless post-mortem: what happened, impact, timeline, root cause, what to change. Keep them in `docs/postmortems/`.

## Customer data requests
Export and deletion requests are legal obligations with deadlines ([privacy policy](../legal/privacy-policy.md)). Make them self-service; the admin console shows pending ones (phase 3).

## Change management
Every production change goes through a pull request and CI. Announce customer-visible changes in a changelog page. Never edit production by hand; if you must, record it and bring the code back in line the same day.

## Costs
Review Cost Explorer monthly; the usual surprises are public IPv4 addresses, data transfer, CloudWatch logs and email/SMS volume ([observability](../aws/08-observability.md)).
