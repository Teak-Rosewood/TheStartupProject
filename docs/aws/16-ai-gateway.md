# 16 · AI gateway and uploads setup

## Provider keys
1. Create one secret by hand, so keys never pass through CloudFormation:
   ```bash
   aws secretsmanager create-secret --region ap-south-1 --name /tsp-prod/ai/keys \
     --secret-string '{"GROQ_KEY":"...","OPENROUTER_KEY":"..."}'
   ```
   The name is `/{prefix}/ai/keys`; `prefix` is `tsp-prod` for production.
2. List the field names in `config/prod.py`: `ai=AiConfig(provider_keys=["GROQ_KEY", "OPENROUTER_KEY"])`. ECS injects each as an environment variable. Deploy.
3. The task role can read the secret only if it is created before the deploy.

Other `AiConfig` settings: `credits_per_1k_in`, `credits_per_1k_out` (what customers are charged, in credits), `default_rpm`, `verify_free_only`.

## Uploads
Set `uploads=UploadsConfig(enabled=True, max_mb=10, org_quota_mb=1024)`. Deploy creates a private bucket and gives the API task read, write and delete on `uploads/*`. See [product operations](../modules/product-ops.md).

## Web app sign-in
The stack outputs `UserPoolWebClientId`, `UserPoolIssuer` and `HostedLoginUrl`. The deploy workflow writes them to `/app/config.json`. To test locally, edit `js/packages/web-app/public/config.json` with the same values and add `http://localhost:5173/app/callback` as a callback URL on the web client.

## Gateway reference
Code: `python/apps/api/src/tsp_api/ai.py`.

| Route | Auth | What it does |
|---|---|---|
| `POST /api/v1/chat/completions` | API key with scope `chat` | OpenAI-compatible chat, streaming or not. `model` can be `auto`, `auto:smart`, `auto:fast`, `auto:cheap`, `auto:reliable` or a pinned model id |
| `POST /api/v1/embeddings` | API key with scope `embed` | OpenAI-compatible embeddings. `model` is required (no `auto`); `input` is a string or up to 256 strings; optional `dimensions` and `input_type` (`document` or `query`) |
| `GET /api/v1/models` | API key with scope `chat` | models in the current verified catalog |

Create keys with the scope list `["chat"]`, `["embed"]` or both (web app: API keys page, or `POST /api/orgs/{id}/api-keys`).

**Checks, in order, and the error each returns** (`{"error": {"type", "message"}}`):

| Check | Status | `type` |
|---|---|---|
| key missing, unknown or revoked | 401 | `invalid_api_key` |
| key lacks the route's scope (`chat` or `embed`) | 403 | `insufficient_scope` |
| kill switch `kill:ai` is on (admin console, flags) | 503 | `feature_disabled` |
| organization has fewer credits than `min_balance` (1) | 402 | `insufficient_credits` |
| over the per-minute limit | 429 | `rate_limited` |
| moderation hook rejects the messages | 400 | `content_blocked` |
| provider failed | provider's status | `upstream_error` |

**Rate limit:** per organization, per minute. The plan feature `ai_rpm` in `plans.toml` sets it; plans without it use `AI_DEFAULT_RPM` (`AiConfig.default_rpm`, default 20).

**Metering and charging:** every request writes one `usage_records` row with meter name `ai.chat` (tokens in, tokens out, credits, latency, status `ok`, `error` or `blocked`). Credits are `ceil(tokens/1000 × rate)` with a minimum charge, using `AI_CREDITS_PER_1K_IN`, `AI_CREDITS_PER_1K_OUT` and `AI_MIN_CREDITS_PER_REQUEST`. Provider errors cost nothing. A request that overruns the balance takes what is left; the next one is refused. Streams report no token counts, so they are charged from an estimate (about 4 characters per token), even if the client disconnects.

**Embeddings** use the same checks, kill switch, credit balance and per-minute limit as chat, and write `usage_records` rows with meter name `ai.embed` (tokens in; credits are `ceil(tokens/1000 × AI_CREDITS_PER_1K_IN)` with the minimum charge). Providers that report no token usage (Google, Cohere on Bedrock) are charged from the 4-characters-per-token estimate. A model that no provider serves returns 404 `upstream_error`; a provider returning vectors of the wrong length returns 502, and nothing is truncated.

**Setting up embedding models:** a model is routable only if it is listed for a provider. With a catalog (`tsp-ai verify` probes embedding models with one short input and records their `dims`), models that answered are used automatically. Without one, list them: `AiConfig` providers take `"embedding_models": {"text-embedding-3-small": 1536}` (a plain list works when dims are unknown). AWS Bedrock (Titan Text Embeddings v2, Cohere Embed multilingual) uses the task role instead of a key: install `tsp-ai[bedrock]`, set `BEDROCK_KEY` to any value to switch the provider on, and allow `bedrock:InvokeModel` on the task role.

**Costs:** the gateway adds no AWS cost. Provider charges depend on the keys you add; free-tier providers are rate limited, which the router handles by falling back across keys and providers.
