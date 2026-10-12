# Incident response and breach notification

> **Template, not legal advice.** This is a starting point written for a software startup based in India that also serves customers abroad. Replace every placeholder written in double curly braces (see `company.example.toml`), delete sections that do not apply, and have a qualified lawyer in your jurisdiction review it before you publish. Laws change; items marked **(verify)** are especially time-sensitive.

Internal procedure. Keep a printed or offline copy of contacts: you may be locked out of email and chat during an incident.

## 1. Severity
| Level | Meaning | Response |
|---|---|---|
| SEV1 | confirmed or likely compromise of personal data, credentials or production; outage of sign-in or payments | start now, any hour |
| SEV2 | major feature down or degraded for many users; security flaw with no known exploitation | within 1 hour in business hours |
| SEV3 | minor impact or workaround exists | next business day |

## 2. Roles
Incident lead (decides, communicates), engineer (investigates and fixes), scribe (timeline), legal/privacy contact ({{dpo_email}}). In a small team one person may hold several roles; the lead must not also be the only person debugging.

## 3. Steps
1. **Detect and declare.** Alarm, customer report ({{security_email}}) or staff report. Open an incident channel and note the time you became aware.
2. **Contain.** Revoke exposed keys and sessions, disable affected accounts or features (kill switches), block offending IPs, snapshot evidence **before** changing things. Do not delete logs.
3. **Assess.** What data, whose, how many people, since when, is it still happening.
4. **Notify (clocks start when you become aware):**
   - **CERT-In (India):** certain cyber incidents must be reported within **6 hours** of noticing them **(verify the current list and form)**.
   - **Data Protection Board of India and affected people:** under the DPDP Act and its rules, notify of a personal data breach in the prescribed form and timeline **(verify the current rules)**.
   - **EU/UK supervisory authority:** within **72 hours** of becoming aware unless unlikely to risk individuals; notify affected people without undue delay if the risk is high.
   - **Customers (as processor):** within {{breach_notice_hours}} hours under the DPA.
   - **Payment provider / card networks / app stores:** as their agreements require.
5. **Recover.** Restore from backup if needed (docs/aws/12-runbooks.md), rotate secrets, verify integrity, monitor for recurrence.
6. **Review.** Blameless post-mortem within 5 working days: timeline, root cause, what worked, actions with owners and dates. Update runbooks and the risk register.

## 4. Communication template (customers)
> **What happened:** … **When:** … **What data:** … **What we have done:** … **What you should do:** … **Contact:** {{security_email}}.

Be factual, say what you do not yet know, and promise the next update time.

## 5. Contacts
Fill in: incident lead, backup lead, legal counsel, cloud account root contact, payment provider support, domain registrar, CERT-In (incident@cert-in.org.in **(verify)**).
