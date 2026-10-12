# `tsp.ai` — multi-provider LLM client

One interface over 24 providers (OpenAI, Anthropic, Google Gemini, Groq, Cohere, Mistral, Cerebras, NVIDIA NIM, OpenRouter, GitHub Models, Hugging Face, Ollama and more; see `providers/registry.py`). Inspired by [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi).

- Routing by reliability, speed and intelligence, with `auto`, `auto:smart`, `auto:fast`, `auto:cheap`, `auto:reliable`, or a pinned model id
- Fallback across keys, models and providers; rate-limit tracking
- Streaming and tool calling (OpenAI format)
- A **verified model catalog**: `tsp-ai verify` lists each provider's models, probes each with one tiny chat, and records which answered
- Sync client and an optional OpenAI-compatible HTTP server

## Install

```bash
uv add ../TheStartupProject/python/packages/ai            # library
uv add "../TheStartupProject/python/packages/ai[server]"  # + HTTP server
```

## Configure keys

Each provider reads `<PLATFORM>_KEY` (for example `GROQ_KEY`, `OPENROUTER_KEY`). Copy `.env.example` to `.env` and fill in the ones you have. Real environment variables win over the file.

## Verify, then use

```bash
tsp-ai verify --env-file .env --free-only    # probe only models known to be free
tsp-ai verify --provider groq -v             # one provider, print every model
```

Each model gets a status: `ok`, `auth`, `quota_or_billing`, `rate_limited`, `model_unavailable`, `bad_request`, `timeout`, `server_error` or `empty_reply`. The catalog is written to `~/.cache/tsp-ai/catalog.json` (`--out` to change). Without `--free-only`, **every** listed chat model is probed, which can cost money on paid providers.

```python
from tsp.ai import LLMClient

client = LLMClient.from_env(".env", catalog="~/.cache/tsp-ai/catalog.json")
resp = await client.chat.create(model="auto", messages=[{"role": "user", "content": "Hi"}])
print(resp.choices[0].message.content, resp.routed_via)

async for chunk in client.chat.create(model="auto:fast", messages=[...], stream=True):
    print(chunk.choices[0].delta.content or "", end="")
await client.close()
```

With a catalog, only models that answered are routable and their latency seeds the speed score. A missing or corrupt catalog raises an error. Without a catalog, pass models yourself: `LLMClient(providers={"groq": {"api_key": "...", "models": ["llama-3.3-70b-versatile"]}})`.

## Embeddings

```python
resp = await client.embeddings.create(
    model="text-embedding-3-small",       # required: no "auto", vectors from different models are incompatible
    input=["first text", "second text"],
    dims=1536,                            # optional; also checked against the catalog's recorded dims
    input_type="document",                # or "query" (Cohere, Google and Cohere-on-Bedrock use it)
    batch_size=96,
)
resp.data[0].embedding, resp.dims, resp.usage.total_tokens
```

Vectors come back in input order. Fallback goes only across keys and providers that serve the same model id, never to a different model. A vector whose length differs from `dims` (or the catalog's) raises `EmbeddingDimensionError`; nothing is truncated or padded. Providers: OpenAI-compatible (`/embeddings`), Cohere, Google (`batchEmbedContents`) and AWS Bedrock (`pip install 'tsp-ai[bedrock]'`; Titan v2 and Cohere Embed). `tsp-ai verify` also probes each provider's embedding models and stores their `dims` under `embedding_models` in the catalog; without a catalog pass `"embedding_models": {"model-id": dims}` per provider. The gateway route is described in [AI gateway](../aws/16-ai-gateway.md).

## Sync

```python
from tsp.ai import SyncLLMClient

with SyncLLMClient.from_env(".env", catalog=...) as client:
    resp = client.chat.create(model="auto", messages=[...])
    for chunk in client.chat.create(model="auto", messages=[...], stream=True):
        ...
```

It runs the async client on a private loop thread, so it is safe to call from threads and from code that already has a loop.

## HTTP server

```bash
TSP_AI_SERVER_KEY=change-me tsp-ai serve --env-file .env --catalog ~/.cache/tsp-ai/catalog.json
curl -H "Authorization: Bearer change-me" localhost:8000/v1/models
```

Routes: `POST /v1/chat/completions` (JSON or SSE with `stream: true`), `GET /v1/models`, `GET /health`. If `TSP_AI_SERVER_KEY` is set every `/v1` route requires `Authorization: Bearer <key>`. Binding a non-loopback host without a key is refused unless you pass `--insecure`. Point any OpenAI SDK at it; see `packages/ai/examples/server_usage.py`.

## Layout

```
python/packages/ai/src/tsp/ai/
  client.py     LLMClient / SyncLLMClient
  router.py     candidate selection and scoring
  fallback.py   retry and failover loop
  ratelimit.py  sliding-window limits
  discovery.py  list + probe models
  catalog.py    verified catalog (pydantic, JSON)
  config.py     .env loader
  server.py     FastAPI app (extra)
  cli.py        tsp-ai verify / serve
  providers/    base, openai_compat, anthropic, google, cohere, registry
```

Adding an OpenAI-compatible provider is one line in `providers/registry.py`; the client, `from_env` and `verify` pick it up.
