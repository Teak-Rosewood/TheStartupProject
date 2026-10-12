# TheStartupProject

A cloneable kit of building blocks for starting a company's software: pick the modules you need and wire them into your app. Python modules install under one `tsp.*` namespace; JavaScript packages will sit beside them.

| Module | Package | Status | Docs |
|---|---|---|---|
| AI: multi-provider LLM client, router, OpenAI-compatible server | `tsp-ai` (`tsp.ai`) | ready | [docs/modules/ai.md](docs/modules/ai.md) |
| Web / React / React Native | `js/` | planned | [js/README.md](js/README.md) |
| AWS backend: edge, server, auth, SQL, NoSQL, cache, CI/CD | `tsp-infra`, `tsp-server`, `tsp-auth`, `tsp-db`, `tsp-nosql`, `tsp-cache` | designed (docs done, modules in progress) | [docs/aws](docs/aws/README.md) |
| Payments | | planned | |

## Quick start

```bash
git clone <this repo> TheStartupProject
cd TheStartupProject/python
uv sync --all-extras
cp ../.env.example ../.env       # add the provider keys you have
uv run tsp-ai verify --env-file ../.env --free-only
```

Use it from another project:

```bash
uv add ../TheStartupProject/python/packages/ai      # or: pip install -e <path>
```

```python
from tsp.ai import LLMClient

client = LLMClient.from_env(".env", catalog="~/.cache/tsp-ai/catalog.json")
resp = await client.chat.create(model="auto", messages=[{"role": "user", "content": "Hello"}])
```

More: [Getting started](docs/getting-started.md) · [Docs index](docs/README.md)

## Layout

```
python/            uv workspace: packages/<module>/src/tsp/<module>
js/                future JavaScript packages
docs/              guides per module, hosting
.github/           CI
```

## License

MIT
