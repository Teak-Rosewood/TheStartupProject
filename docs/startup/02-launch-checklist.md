# 02 · Launch checklist

> Guidance for founders, not legal, tax or financial advice. Rules (especially Indian tax, data-protection and payment rules) change; items marked **(verify)** need a check with a chartered accountant (CA), company secretary (CS) or lawyer before you act.

Tick each item or write down why not. Items link to where this kit helps.

## Legal and trust
- [ ] Terms, Privacy, Cookie, Refund and AUP published at stable URLs and linked in the footer and sign-up ([templates](../legal/README.md))
- [ ] Legal name, CIN, address, Grievance Officer and support email visible on the site
- [ ] Sign-up records consent to the current document versions (phase 2)
- [ ] [Sub-processors list](../legal/subprocessors.md) matches what the code actually calls, including AI providers
- [ ] Payment gateway live KYC approved (needs the policy pages above, a working site and business documents)
- [ ] App-store privacy forms filled (Google Play data safety, Apple privacy labels); in-app account deletion works (Apple requires it)

## Security
- [ ] Everything in [04 Security baseline](04-security-baseline.md)
- [ ] `SECURITY.md` and `/.well-known/security.txt` name a monitored address
- [ ] Log retention raised for production ([retention schedule](../legal/data-retention.md))

## Reliability
- [ ] First deploy done, rollback rehearsed ([runbooks](../aws/12-runbooks.md))
- [ ] Backup restore drill passed at least once on production data
- [ ] Alarms deliver to a mailbox someone reads; budget alerts set ([observability](../aws/08-observability.md))
- [ ] Basic load test of sign-in and the main API path at 5× expected launch traffic
- [ ] Friendly error pages and a status page ([support and ops](06-support-and-ops.md))

## Product
- [ ] Email deliverability set up: SPF, DKIM, DMARC; test a message to Gmail, Outlook and a corporate inbox
- [ ] GST invoice generated, numbered, emailed and exported correctly for an India customer and for a foreign customer
- [ ] One full paid journey tested live with a real card and UPI, then refunded
- [ ] Analytics events fire for the funnel in [03 Metrics](03-metrics.md), without personal data in event properties

## Go-to-market
- [ ] Landing page: one clear promise, proof, pricing, a call to action ([marketing playbook](07-marketing-playbook.md))
- [ ] Waitlist or launch list with consent recorded
- [ ] Launch posts drafted; first 20 target customers listed by name
- [ ] Support inbox staffed for the launch week

## Day before
- [ ] Freeze changes; confirm on-call person and phone numbers
- [ ] Verify monitoring and billing dashboards open
- [ ] Re-read the [incident response](../legal/incident-response.md) page
