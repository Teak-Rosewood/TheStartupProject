"""One list of every table any module creates, so each test starts from a clean database."""

ALL_TABLES = (
    "leads, events, referral_codes, referrals, emails, email_suppressions, email_preferences, device_tokens, "
    "notifications, jobs, cron_jobs, credit_ledger, credit_accounts, invoices, invoice_sequences, webhook_events, "
    "transfers, refunds, payments, subscriptions, payment_customers, plans, billing_profiles, api_keys, invites, "
    "memberships, orgs, users, consents, data_requests, audit_log, alembic_version, notes, flags, files, usage_records"
)
