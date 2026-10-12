# `tsp.payments` — payments for India first

Razorpay is the default gateway, Stripe the second (customers paying from abroad), behind one interface. Money is always **integer minor units** (paise, cents) plus a currency code.

## What it does
| Piece | Behavior |
|---|---|
| Plans | defined in `plans.toml`, synced into the database on every start; removing one deactivates it (existing subscribers keep it). Kinds: `subscription`, `one_time`, `credit_pack`. |
| Checkout | `POST /orgs/{id}/billing/checkout` (org admins) creates a gateway order or subscription and returns what the browser SDK needs (Razorpay Checkout options, or a Stripe URL). |
| Webhooks | `POST /api/webhooks/{provider}`: the signature is verified over the **raw body** (HMAC-SHA256; Stripe also rejects timestamps older than 5 minutes), every event is stored once by its provider event id, then applied by a state machine. A failure returns 5xx so the gateway retries; a forged request returns 400 and leaves no trace. |
| Entitlements | `entitlements(org)` and the `require_feature("exports")` dependency (402 `upgrade_required`). Active subscriptions are entitled; a failed renewal keeps access for 3 days; a cancelled subscription keeps access until the paid period ends. |
| Credits | an append-only ledger and a balance that cannot go negative. Grants and spends take a `ref` (payment id, request id) so retries never double-apply; concurrent spends are serialized by a row lock. Credit packs grant credits on capture; a full refund claws back unspent credits. |
| Refunds | staff request them (reason required, audited); the local records change when the gateway's refund webhook arrives. |
| GST invoices | one invoice per captured payment, a credit note per refund, numbered `INV/2627/000001` (at most 16 characters, as GST requires), **gapless per financial year** (April–March, judged in IST): the number is allocated in the same transaction as the invoice, so a rollback never burns one. PDF via reportlab (no system libraries), stored in a retained, versioned bucket. CSV export for your accountant. |
| Marketplace | `transfer()` sends part of a captured payment to a seller's linked account (Razorpay Route). The licensed gateway holds the money. |

### Webhook safety rules the tests enforce
- Unknown signature, tampered body, wrong secret, stale Stripe timestamp → rejected.
- Redelivery of the same event, or a second event announcing the same payment (`payment.captured` and `subscription.charged`) → applied once.
- A captured amount different from the order → recorded as `amount_mismatch`, nothing granted, audit entry written.
- An event that cannot be attributed to an organization → error, so the gateway redelivers.
- A failed invoice (storage down) never loses the payment; `issue_missing_invoices()` repairs it (a job runs it in phase 5).

## GST rules encoded (agree them with your CA)
- Tax-inclusive prices. For 18%: ₹1,180.00 → taxable ₹1,000.00 + tax ₹180.00.
- **Intra-state** (buyer state = your state, or a consumer with no state recorded): CGST + SGST in equal halves; an odd paisa stays in the taxable value so the halves match.
- **Inter-state:** IGST. **Export** (buyer outside India): zero-rated when `lut = true` (you filed a Letter of Undertaking), otherwise IGST is charged.
- Seller and buyer GSTINs are validated (format and check character); a buyer GSTIN fixes the state of supply.
- Not built: e-invoicing (IRN) above the mandatory turnover threshold, TDS/TCS, reverse charge, foreign-currency conversion to INR on export invoices.

## Setting up Razorpay
1. Create the account, complete KYC (needs your published Terms, Privacy and Refund pages; see [legal](../legal/README.md)).
2. **Test mode first.** Create a plan under Subscriptions → Plans for each subscription and paste its id into `plans.toml` (`provider_plan_id`).
3. Settings → API keys: create test keys. Settings → Webhooks: add `https://<your-domain>/api/webhooks/razorpay` with a secret you generate and the events `payment.captured`, `payment.failed`, `subscription.activated`, `subscription.charged`, `subscription.pending`, `subscription.halted`, `subscription.cancelled`, `subscription.completed`, `subscription.paused`, `subscription.resumed`, `refund.processed`, `refund.failed` (and `transfer.processed`, `transfer.failed` for marketplaces).
4. Store the credentials ([aws/14-payments](../aws/14-payments.md)) and set `payments.razorpay = True`.
5. **Run the full test-mode checklist** before going live: a one-time purchase, a subscription purchase and its renewal, a failed payment, a refund, and a cancellation. Check after each that the webhook was processed (`webhook_events`), the entitlement changed, the invoice PDF is correct, and the audit log has the entries.
6. Repeat with live keys and a small real payment you refund.

> The request and event shapes follow Razorpay's and Stripe's public documentation and are covered by tests with recorded-shape payloads. They have **not** been exercised against the live sandbox; step 5 is how you close that gap.

## Recurring payments in India
Card and UPI AutoPay mandates follow RBI e-mandate rules: the customer authenticates when the mandate is set up, a pre-debit notification is sent, and charges above the per-transaction limit (₹15,000 for most categories **(verify)**) need extra authentication each time. For high-value plans use invoices or annual prepayment. See [pricing and billing](../startup/05-pricing-and-billing.md).

## Marketplaces
Holding customers' money and paying sellers later is payment aggregation, which needs RBI authorization. The supported route is a gateway's marketplace product (Razorpay Route): sellers complete the gateway's KYC (`create_linked_account` passes Razorpay's documented body through untouched), buyers pay the platform's order, and `transfer()` splits it. GST on marketplaces has special rules (TCS for e-commerce operators). **Get a lawyer's and a CA's opinion before launching payouts.**

## Wiring
```python
payments = Payments({"razorpay": RazorpayProvider(key_id, key_secret, webhook_secret)},
                    default_provider="razorpay", invoicer=Invoicer(seller, S3InvoiceStore(bucket)))
app.include_router(payments_api.build_router(payments, accounts_api.account, accounts_api.org_member, db_session), prefix="/api")
```
`python/apps/api/src/tsp_api/billing.py` builds all of it from environment variables. Migrations: `tsp-db migrate --module tsp.payments`.

## Staff console
Finance and superadmin see payments, subscriptions, invoices, refunds and transfers; finance can request a refund (reason required) and download a financial year's invoice CSV; support sees payments and subscriptions only. Every action is audited ([accounts and admin](accounts-admin.md)).
