# Data retention schedule

> **Template, not legal advice.** This is a starting point written for a software startup based in India that also serves customers abroad. Replace every placeholder written in double curly braces (see `company.example.toml`), delete sections that do not apply, and have a qualified lawyer in your jurisdiction review it before you publish. Laws change; items marked **(verify)** are especially time-sensitive.

How long each kind of data stays, and why. Engineering should implement these as automatic jobs, not manual tasks. Confirm legal periods with your chartered accountant and counsel **(verify)**.

| Data | Retention | Why / notes |
|---|---|---|
| Account and profile | life of the account, plus a 30-day grace period after deletion is requested | contract; lets users undo accidental deletion |
| Content users create | life of the account; removed from live systems within 30 days of deletion | contract |
| Consent records (which document version, when) | life of the account plus 3 years | proof of consent |
| Audit log of administrative actions | 1 year online, then archived up to 3 years | security and accountability |
| Application and security logs | {{log_retention_days}} days in India | Indian CERT-In directions require retaining ICT logs for a rolling 180 days within India **(verify applicability)**; the stack default is shorter and must be raised for production |
| Tax invoices and payment records | 8 years from the end of the financial year **(verify with your CA)** | GST and company law; these are not deleted when an account is deleted, only de-identified where possible |
| Database backups (continuous WAL plus weekly full) | about 4 weeks | disaster recovery; deleted data leaves backups within this cycle |
| EBS volume snapshots | 7 days | disaster recovery |
| Finished background jobs | 14 days | operations |
| Payment webhook payloads | 90 days | debugging; may contain email and contact number |
| Email outbox (recipient, template, status) | 13 months | delivery proof; recipient is replaced by a hash when the user is deleted |
| Analytics events (first-party) | 400 days | product analytics; no personal data in properties; linked to an account only after sign-in |
| Waitlist signups | confirmed: until unsubscribe; unconfirmed: deleted after 30 days | consent (double opt-in) |
| Uploaded files | life of the account; removed with the account | contract; stored per organization in a private bucket |
| AI usage records (model, tokens, credits; no prompt text) | 13 months | billing disputes and support |
| AI model catalog | replaced daily | operations; contains no personal data |
| Search index (passages and vectors of documents users add) | life of the document or account; removed within the same request as the document | contract; the passages are copies of the user's content |
| Paywall reading history (signed-in readers) | 13 months, or until the account is deleted | applying the free-reads window; anonymous meters are not linked to a person |
| Support conversations | 2 years after closure | support quality; legal claims |
| Marketing leads and waitlist | until unsubscribe, plus 30 days; double opt-in proof for 3 years | consent |
| Inactive accounts | warn at 12 months of inactivity, delete after 24 **(optional policy)** | data minimisation |
| Payment card data | never stored by us | held by the payment provider |

Data-subject requests (export, deletion) override these schedules except where the law requires retention.
