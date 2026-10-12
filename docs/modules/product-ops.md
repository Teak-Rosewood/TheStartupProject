# Product operations: flags, uploads, usage metering, AI gateway

Four small modules that most products need once real customers arrive. All are wired into `python/apps/api` and migrate with the others (`TSP_MIGRATE_MODULES`).

## Feature flags and kill switches (`tsp.flags`)
- A flag has a master switch, a rollout percentage, allow and deny lists (users, orgs), and plan rules. Evaluation order: master switch, deny lists, allow lists, plan, rollout percentage. The percentage bucket is a stable hash of the flag key and the org (or user), so raising it only adds people.
- A flag named `kill:<name>` is a kill switch. Code guards risky features with `require_not_killed("<name>")`; switching it on makes the feature refuse requests within about 30 seconds (the per-process cache time).
- `GET /api/flags` returns the flags marked `client_visible`, evaluated for the caller.
- Staff change flags in the admin console. Superadmins can change any flag; support staff can only change `kill:*` switches. Every change is audited with a reason.

## File uploads (`tsp.storage`)
- The browser asks the API for a presigned S3 POST (`/api/orgs/{id}/files`). S3 itself enforces the size limit, content type and key prefix, so file bytes never pass through the API.
- Files live under a per-organization prefix with a per-organization quota (`UPLOAD_MAX_MB`, `UPLOAD_ORG_QUOTA_MB`).
- A scan hook lets you plug in malware scanning before a file becomes available. None is bundled; add one before accepting files from untrusted users.
- Enable with `uploads=UploadsConfig(enabled=True)` in the environment config. The bucket is private, retained on stack deletion, and CORS allows only your app origin.
- Files are exported and erased with the user's other data (see [compliance](compliance.md)).

## Usage metering (`tsp.metering`)
- `usage_records` is an append-only log of metered events per organization. `Rates` converts tokens to credits (rounded up, with a minimum charge). `GET /api/orgs/{id}/usage` shows recent usage.
- Credits are charged against the payments credits ledger, so prepaid plans and usage billing share one balance.

## AI gateway (`apps/api`, `tsp_api/ai.py`)
- `POST /api/v1/chat/completions` and `GET /api/v1/models`, OpenAI-compatible, authenticated with a customer API key that has the `chat` scope.
- Order of checks: kill switch `kill:ai`, key and scope, per-plan requests per minute (`ai_rpm` plan feature, default 20), credit balance, optional moderation hook, then the call. Successful calls are charged by token usage; provider errors are not charged. Streaming responses are charged by estimate once the stream ends.
- Provider keys come from environment variables (`GROQ_KEY` and so on). In AWS they are injected from one Secrets Manager secret; see [AI gateway setup](../aws/16-ai-gateway.md).
- A daily job (04:10) re-probes models and stores the working list in the `ai_catalog` table, so a model that disappears stops being routed to. Set `AI_VERIFY_FREE_ONLY=0` to probe paid models too (this costs a few tokens each).

## Not covered
Malware scanning, content moderation providers, quota alerts to customers, and per-model pricing tables are hooks or follow-ups, not built.
