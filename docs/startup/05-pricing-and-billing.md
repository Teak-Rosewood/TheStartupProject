# 05 · Pricing and billing

> Guidance for founders, not legal, tax or financial advice. Rules (especially Indian tax, data-protection and payment rules) change; items marked **(verify)** need a check with a chartered accountant (CA), company secretary (CS) or lawyer before you act.

## Pricing basics
- Charge for the **value metric** customers feel (seats, projects, API calls, outcomes), not your cost. Offer 3 tiers: a small one that captures the long tail, a "recommended" middle, and a large one that makes the middle look reasonable.
- Offer annual billing at about 15–20% off for cash up front and lower churn.
- Start higher than feels comfortable; it is easier to discount than to raise prices.
- For AI features, price from **cost plus margin**: know tokens per request and your provider's price, and cap usage per plan. Consider prepaid credits for heavy users ([metrics](03-metrics.md)).

## India: GST and invoices
- SaaS and software services are generally taxed at 18% GST **(verify rate and SAC code with your CA)**. Charge **CGST+SGST** when you and the customer are in the same state, **IGST** across states.
- A tax invoice must carry specific fields: your GSTIN, a **consecutive serial number unique for the financial year (April–March)**, date, customer name and GSTIN (for B2B), place of supply, SAC code, taxable value, tax amounts. The payments module generates invoices with a gapless per-year series.
- Show consumer prices **inclusive of GST**; show business prices exclusive with tax added at checkout, and say which you are doing.
- **E-invoicing (IRN):** mandatory for businesses above a turnover threshold that has been lowered over the years **(verify current threshold)**. Not built into this kit.
- **Exports:** invoice foreign customers without GST under an LUT ([company setup](01-company-setup.md)); keep proof of foreign-currency receipt.
- Issue a **credit note** for refunds after an invoice.

## Taking payments in India
- Use a licensed payment gateway (Razorpay is the default integration). It supports UPI, cards, netbanking, wallets and subscriptions, and handles card data so you stay out of PCI scope.
- **Recurring payments** by card or UPI AutoPay follow RBI e-mandate rules: customers authenticate when setting up the mandate, you must send a pre-debit notification, and transactions above a per-transaction limit (₹15,000 for most categories **(verify)**) need extra authentication each time. For higher-value plans, consider payment links, invoices, or annual prepayment.
- Live activation needs your website to show terms, privacy and refund pages plus business documents. Do this before launch week.
- **Chargebacks and disputes:** keep proof of delivery and usage logs; respond within the gateway's deadline.

## Taking payments from abroad
Stripe or the gateway's international option may need your company to be eligible and to use purpose codes; settlement arrives in INR with fees and conversion. Ask your bank and CA before you promise USD pricing **(verify)**.

## Mobile apps
Apple requires its in-app purchase system for digital goods and services consumed in an iOS app, with exceptions that vary by country and have been changing; Google Play has similar billing rules **(verify current guidelines)**. Selling physical goods or real-world services is exempt. Plan pricing around the store commission if you sell subscriptions in the app, and keep web checkout for web users.

## Marketplaces and holding other people's money
If your platform collects money from buyers and pays sellers later, Indian rules treat that as payment aggregation, which needs RBI authorization. The usual startup route is a gateway's marketplace product (for example Razorpay Route or Cashfree Easy Split) where the licensed gateway holds funds and splits payouts. Sellers must complete KYC. GST has special rules for e-commerce operators (collecting tax at source) **(verify with your CA)**. Get a lawyer's opinion before you launch payouts.

## Refunds
Publish a clear [refund policy](../legal/refund-cancellation.md) and follow it every time; it is required for gateway approval and reduces chargebacks.
