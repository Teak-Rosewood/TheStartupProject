# 04 · Security baseline

> Guidance for founders, not legal, tax or financial advice. Rules (especially Indian tax, data-protection and payment rules) change; items marked **(verify)** need a check with a chartered accountant (CA), company secretary (CS) or lawyer before you act.

A small team's minimum before real users arrive. Tick them off; most take an afternoon.

## People and accounts
- [ ] MFA on every account that can reach production or money: email, GitHub, AWS, domain registrar, payment gateway, app-store accounts, password manager. Use authenticator apps or security keys, not SMS.
- [ ] A team password manager; no shared passwords in chat or documents.
- [ ] AWS: root user locked away with MFA; humans use SSO roles; no long-lived access keys ([account setup](../aws/00-account-setup.md)).
- [ ] Laptops: full-disk encryption, screen lock, automatic updates.
- [ ] Offboarding checklist: revoke GitHub, AWS, email, gateway, registrar access the same day.

## Code and supply chain
- [ ] Branch protection on `master`, required reviews, required CI checks.
- [ ] Secret scanning and dependency audit run in CI (`.github/workflows/security.yml`); Dependabot enabled.
- [ ] No secrets in the repo or in container images. Secrets live in AWS Secrets Manager.
- [ ] Pin and review GitHub Actions; prefer official or well-known actions.

## Application
- [ ] All traffic over HTTPS; HSTS and security headers on (the CloudFront policy already sets them).
- [ ] Authentication handled by Cognito; the API never stores passwords.
- [ ] Authorization checked on the server for every resource (organization scoping), not only in the UI.
- [ ] Input validated with typed models; parameterized queries only.
- [ ] Rate limits and CAPTCHA on sign-up, login help and public forms.
- [ ] File uploads restricted by type and size and stored outside the web root.
- [ ] Webhooks verified by signature and processed idempotently.

## Data
- [ ] Encryption at rest and in transit (default in this stack).
- [ ] Backups tested by restore, monthly ([workflow](../../.github/workflows/backup-drill.yml)).
- [ ] Production data never copied to laptops or used in dev.
- [ ] Retention schedule implemented as jobs ([schedule](../legal/data-retention.md)).

## Detection and response
- [ ] Alarms reach a human ([observability](../aws/08-observability.md)); an anomaly alert on AWS spend.
- [ ] Audit log of administrative actions (phase 2).
- [ ] [Incident response](../legal/incident-response.md) page read by everyone; contacts printed.
- [ ] A way for researchers to reach you: `SECURITY.md`, `security.txt`.

## Later, when customers ask
Independent penetration test; SOC 2 Type I then II or ISO 27001; vendor-risk questionnaires; SSO/SAML for enterprise customers; a data-processing agreement ([template](../legal/dpa.md)).
