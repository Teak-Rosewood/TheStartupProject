# What this kit covers, and what is missing

Status as of the last update to this file. "Planned" items are scheduled in the order listed in the roadmap below.

| Area | Status | Where |
|---|---|---|
| AI: multi-provider LLM client, router, OpenAI-compatible server | done | `python/packages/ai`, [docs/modules/ai.md](../modules/ai.md) |
| AWS backend: edge, compute, auth, SQL, NoSQL, cache, CI/CD, runbooks | built, awaiting first live deploy | [docs/aws](../aws/README.md) |
| Repo hygiene: license, security policy, contributing, templates, secret/dependency scanning | done | repo root, `.github/` |
| Legal templates | done (needs your details and a lawyer) | [docs/legal](../legal/README.md) |
| Startup guides (this folder) | done | here |
| Consent records, data export and deletion, audit log | done | `tsp.audit`, `tsp.compliance`, [docs](../modules/compliance.md) |
| Users, organizations, roles, invites, customer API keys | done | `tsp.accounts`, [docs](../modules/accounts-admin.md) |
| Admin console with separate MFA login | done (needs a live check) | `tsp.admin`, [docs](../modules/accounts-admin.md) |
| Payments for India (Razorpay), GST invoices, credits ledger, Stripe, marketplace splits | built and tested against recorded payloads; needs a gateway test-mode run | `tsp.payments`, [docs](../modules/payments.md) |
| Background jobs, email, push, SMS, in-app | built and tested; needs SES setup and a live send | `tsp.jobs`, `tsp.notify`, [docs](../modules/jobs-notify.md) |
| Landing page, SEO, waitlist, events, attribution, referrals, metrics | built and verified; needs your copy, a real OG image and Lighthouse in CI | `js/packages/landing`, `tsp.growth`, [docs](../modules/growth.md) |
| Feature flags, file uploads, AI usage metering, web and mobile starters | planned, phase 7 | `tsp.flags`, `tsp.storage`, `js/` |

## Known gaps in the repo today
- The first live deployment has not happened; runbooks and costs are unverified against a real account.
- CloudWatch logs are kept 14 days. Indian CERT-In directions expect security-relevant logs for a rolling 180 days **(verify)**; raise it before production (made configurable in phase 2).
- `tsp.ai` is not yet mounted in `tsp.server`; there is no per-customer quota or usage billing.
- There is no frontend beyond a placeholder page.
- No SOC 2 / ISO 27001 material, no penetration-test guidance beyond the checklist, no multi-region design.

## Not covered at all (decide deliberately)
Customer-support desk and CRM, accounting software integration beyond CSV export, WhatsApp Business messaging, e-invoicing (IRN) generation for GST turnover above the mandatory threshold **(verify)**, a data warehouse or BI tool, internationalisation, and accessibility audits beyond the landing template defaults.
