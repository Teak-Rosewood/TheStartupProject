"""AWS Bedrock adapter: embeddings only (Titan Text Embeddings v2, Cohere Embed on Bedrock).

Authenticates with the AWS credential chain (task role, profile, env), not an API key, so register it
with ``providers={"bedrock": {"api_key": "iam", "embedding_models": {...}}}``. Needs ``boto3``
(``pip install 'tsp-ai[bedrock]'``).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from typing import Any

from .base import BaseProvider, ProviderHttpError

TITAN = "amazon.titan-embed-text-v2:0"
COHERE = "cohere.embed-multilingual-v3"
KNOWN_MODELS = (TITAN, COHERE)


class BedrockProvider(BaseProvider):
    def __init__(self, region: str | None = None):
        self._region = region or os.environ.get("AWS_REGION", "ap-south-1")
        self._runtime: Any = None

    @property
    def platform(self) -> str:
        return "bedrock"

    @property
    def name(self) -> str:
        return "AWS Bedrock"

    @property
    def keyless(self) -> bool:
        return True

    def _client(self) -> Any:
        if self._runtime is None:
            try:
                import boto3
            except ImportError:
                raise ProviderHttpError(
                    "boto3 missing: pip install 'tsp-ai[bedrock]'", status=400
                ) from None
            self._runtime = boto3.client("bedrock-runtime", region_name=self._region)
        return self._runtime

    def _invoke(self, model_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            out = self._client().invoke_model(
                modelId=model_id,
                body=json.dumps(body),
                accept="application/json",
                contentType="application/json",
            )
            return json.loads(out["body"].read())
        except ProviderHttpError:
            raise
        except Exception as e:
            meta = getattr(e, "response", {}).get("ResponseMetadata", {})
            status = meta.get("HTTPStatusCode") or 500
            raise ProviderHttpError(f"Bedrock: {e}", status=status) from e

    async def embedding(
        self,
        api_key: str,
        input: str | list[str],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Options: ``input_type`` (Cohere), ``dims`` (Titan: 256, 512 or 1024)."""
        options = options or {}
        texts = [input] if isinstance(input, str) else input
        if model_id.startswith("cohere."):
            body = {
                "texts": texts,
                "input_type": "search_query"
                if options.get("input_type") == "query"
                else "search_document",
            }
            data = await asyncio.to_thread(self._invoke, model_id, body)
            vectors = data.get("embeddings", [])
            tokens = 0  # Cohere on Bedrock reports no usage
        else:  # Titan takes one text per call
            body_for = lambda t: {  # noqa: E731
                "inputText": t,
                "normalize": True,
                **({"dimensions": options["dims"]} if options.get("dims") else {}),
            }
            results = await asyncio.gather(
                *(asyncio.to_thread(self._invoke, model_id, body_for(t)) for t in texts)
            )
            vectors = [r.get("embedding", []) for r in results]
            tokens = sum(
                int(r.get("inputTokenCount") or r.get("inputTextTokenCount") or 0) for r in results
            )
        return {
            "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)],
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        }

    async def list_models(self, api_key: str) -> list[dict[str, Any]]:
        return [{"id": m} for m in KNOWN_MODELS]

    async def chat_completion(self, api_key, messages, model_id, options=None) -> dict[str, Any]:
        raise NotImplementedError("bedrock provider supports embeddings only")

    def stream_chat_completion(
        self, api_key, messages, model_id, options=None
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError("bedrock provider supports embeddings only")

    async def validate_key(self, api_key: str) -> bool | dict[str, Any]:
        return True
