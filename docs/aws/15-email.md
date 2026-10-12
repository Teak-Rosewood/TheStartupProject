# 15 · Email setup (Amazon SES)

## 1. Enable it
```python
notify=NotifyConfig(enabled=True, domain="example.com", from_address="no-reply@example.com",
                    brand="Acme", footer="Acme Technologies Pvt Ltd, 1 Example Street, Bengaluru 560001")
```
Deploy. The stack creates a domain identity with DKIM and a custom MAIL FROM domain (`mail.example.com`), two configuration sets (`transactional`, `marketing`), an SNS topic and an SQS queue (with a dead-letter queue) for bounce and complaint events, and a signing secret for unsubscribe links.

## 2. Add DNS records at your registrar
The deploy outputs three DKIM `CNAME` name/value pairs (`DkimName1..3`, `DkimValue1..3`). Add them. For the MAIL FROM domain add, as shown in the SES console for the identity: an `MX` record for `mail.example.com` and a `TXT` SPF record for it. Also add:
- **SPF** on the root: `v=spf1 include:amazonses.com ~all` (merge with any existing SPF record; only one is allowed).
- **DMARC** (start monitoring-only): `_dmarc.example.com TXT "v=DMARC1; p=none; rua=mailto:dmarc@example.com"`; move to `p=quarantine`, then `p=reject`, once reports look clean.

Wait for the identity to show *Verified* and DKIM *Successful* in the SES console.

## 3. Leave the sandbox
New SES accounts can only send to verified addresses. Request **production access** for `ap-south-1` (console → SES → Account dashboard). Describe the use honestly: transactional mail (receipts, invitations, security), marketing only to opted-in users with one-click unsubscribe, and how you handle bounces and complaints (this stack suppresses them automatically). Approval takes about a day.

## 4. Test before launch
Send each template to a Gmail, an Outlook and a company inbox; check the message is not in spam, the `From` matches, SPF/DKIM/DMARC pass (view the original headers), and the unsubscribe link works. Use the SES *mailbox simulator* addresses (`bounce@simulator.amazonses.com`, `complaint@simulator.amazonses.com`) to confirm bounces and complaints reach the suppression table within a minute or two.

## 5. Keep the reputation healthy
Watch bounce rate (keep under about 2%) and complaint rate (under 0.1%) in the SES reputation dashboard; SES pauses sending above its limits. Never mail purchased lists; marketing needs recorded opt-in ([metrics and marketing](../startup/07-marketing-playbook.md)).

## 6. Cognito sender
Set `auth.ses_from` to the same verified address so sign-up and password mails come from your domain instead of Cognito's 50-per-day built-in sender.

## Cost
SES: about $0.10 per 1,000 emails. SQS, SNS: free tier. Secrets Manager: $0.40 for the signing secret.
