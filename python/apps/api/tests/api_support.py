"""One list of every table any module creates, so each test starts from a clean database."""

ALL_TABLES = (
    "leads, events, referral_codes, referrals, emails, email_suppressions, email_preferences, device_tokens, "
    "notifications, jobs, cron_jobs, credit_ledger, credit_accounts, invoices, invoice_sequences, webhook_events, "
    "transfers, refunds, payments, subscriptions, payment_customers, plans, billing_profiles, api_keys, invites, "
    "memberships, orgs, users, consents, data_requests, audit_log, alembic_version, notes, flags, files, usage_records, ai_catalog, ai_batches, "
    "vector_chunks, vector_documents, vector_collections, crawl_pages, crawl_sources, "
    "pw_policies, pw_reads, pw_gifts, pw_credits, pw_unlocks, "
    "cms_complaint_events, cms_complaints, cms_windows, cms_corrections, cms_reviews, "
    "cms_disclosure_templates, cms_check_results, cms_variants, cms_items, cms_versions, "
    "en_submissions, en_onboarding, en_badges, en_points, en_streaks, en_receipts, en_responses, en_polls, "
    "pl_eval_runs, pl_events, pl_steps, pl_prompt_active, pl_prompts, pl_runs"
)
