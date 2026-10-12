# Legal documents

Fill-in templates for the documents a launching software startup is usually expected to publish, plus the machinery to keep them consistent with the product.

> **Template, not legal advice.** This is a starting point written for a software startup based in India that also serves customers abroad. Replace every placeholder written in double curly braces (see `company.example.toml`), delete sections that do not apply, and have a qualified lawyer in your jurisdiction review it before you publish. Laws change; items marked **(verify)** are especially time-sensitive.

| Document | File | Needed for |
|---|---|---|
| Terms of Service | [terms-of-service.md](terms-of-service.md) | every product; payment-gateway KYC |
| Privacy Policy | [privacy-policy.md](privacy-policy.md) | every product; app stores; payment-gateway KYC |
| Cookie Policy | [cookie-policy.md](cookie-policy.md) | any website with non-essential cookies or analytics |
| Acceptable Use Policy | [acceptable-use.md](acceptable-use.md) | user content, AI, marketplaces |
| Refund and Cancellation Policy | [refund-cancellation.md](refund-cancellation.md) | anything you charge for; payment-gateway KYC |
| Data Processing Agreement | [dpa.md](dpa.md) | B2B customers who put personal data into your product |
| Sub-processors | [subprocessors.md](subprocessors.md) | DPA, privacy policy |
| Data retention schedule | [data-retention.md](data-retention.md) | privacy policy, engineering |
| Incident response and breach notification | [incident-response.md](incident-response.md) | everyone (internal) |

## How to use them
1. Copy `company.example.toml` to `company.toml` and fill it in.
2. Check that no placeholder is undefined: `make legal-check`.
3. (Phase 2) `tsp-compliance render-legal --config company.toml` renders the documents and writes `documents.json` with an id, version and checksum per document. The API records which version each user accepted, and asks them to accept again when a version changes.
4. Publish the rendered documents on your website (the landing template does this) and link them from the sign-up screen, the footer, your payment-gateway dashboard and your app-store listings.

## Why these and not others
Razorpay, Stripe and the app stores check for a published terms page, privacy policy and refund policy before activating live payments or listing. The DPDP Act 2023 (India), GDPR (EU/UK) and CCPA (California) each require a notice describing what personal data you collect, why, who receives it and how to exercise rights. The DPA is what business customers ask for in security reviews.
