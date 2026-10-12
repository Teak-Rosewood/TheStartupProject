# tsp-ai

Multi-provider LLM client with routing, fallback, rate limiting, a verified model catalog and an optional OpenAI-compatible server. Imported as `tsp.ai`.

```bash
tsp-ai verify --env-file .env --free-only
```

```python
from tsp.ai import LLMClient

client = LLMClient.from_env(".env", catalog="~/.cache/tsp-ai/catalog.json")
```

Full guide: [docs/modules/ai.md](../../../docs/modules/ai.md).
