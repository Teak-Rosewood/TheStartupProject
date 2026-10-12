import asyncio
import os
from pathlib import Path

import pytest
import redis.asyncio as redis
from api_support import ALL_TABLES
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from tsp.accounts import Accounts
from tsp.ai import EmbeddingDimensionError, ProviderHttpError
from tsp.ai.models import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    DeltaMessage,
    EmbeddingItem,
    EmbeddingResponse,
    StreamChoice,
    Usage,
)
from tsp.audit import Actor
from tsp.db import Database, upgrade
from tsp.flags import Flags, FlagUpdate
from tsp.metering import Rates, UsageRecord
from tsp.payments import Payments
from tsp.server import Settings, create_app
from tsp_api.ai import AiGateway, build_router

DB = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
CACHE = os.environ.get("TSP_TEST_CACHE_URL", "redis://127.0.0.1:56379/0")
MIGRATIONS = Path(__file__).parents[1] / "migrations"
MODULES = ["tsp.audit", "tsp.accounts", "tsp.payments", "tsp.flags", "tsp.metering"]
OWNER = "aaaaaaaa-0000-0000-0000-00000000000a"


class FakeChat:
    def __init__(self):
        self.calls, self.fail, self.usage = (
            [],
            None,
            Usage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000),
        )

    def create(self, *, model, messages, stream=False, **params):
        self.calls.append((model, messages, stream, params))
        if self.fail:
            raise self.fail
        if stream:
            return self._stream()
        return self._once()

    async def _once(self):
        return ChatCompletionResponse(
            model="llama-3",
            routed_via="groq/llama-3",
            usage=self.usage,
            choices=[Choice(message=ChoiceMessage(content="hello there"))],
        )

    async def _stream(self):
        for piece in ("hel", "lo ", "there!!!"):
            yield ChatCompletionChunk(
                model="llama-3",
                routed_via="groq/llama-3",
                choices=[StreamChoice(delta=DeltaMessage(content=piece))],
            )


class FakeEmbeddings:
    def __init__(self):
        self.calls, self.fail, self.tokens = [], None, 500

    async def create(self, model, input, *, dims=None, input_type="document", batch_size=96):
        self.calls.append((model, input, dims, input_type))
        if self.fail:
            raise self.fail
        return EmbeddingResponse(
            model=model,
            provider="openai",
            dims=2,
            data=[EmbeddingItem(index=i, embedding=[0.5, 0.25]) for i, _ in enumerate(input)],
            usage=Usage(prompt_tokens=self.tokens, total_tokens=self.tokens),
        )


class FakeLLM:
    def __init__(self):
        self.chat = FakeChat()
        self.embeddings = FakeEmbeddings()

    def get_models(self):
        return [
            {"model_id": "llama-3", "platform": "groq", "disabled": False},
            {"model_id": "old", "platform": "x", "disabled": True},
        ]


async def _drop():
    d = Database(DB)
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(text(f"DROP TABLE IF EXISTS {ALL_TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    await d.dispose()


@pytest.fixture
def ctx():
    try:
        asyncio.run(_drop())

        async def flush():
            r = redis.from_url(CACHE)
            await r.flushdb()
            await r.aclose()

        asyncio.run(flush())
    except Exception:
        pytest.skip("local services not running (docker compose up -d)")
    asyncio.run(asyncio.to_thread(upgrade, DB, MIGRATIONS, modules=MODULES))
    llm, accounts, flags = FakeLLM(), Accounts(), Flags(ttl=0)
    pay = Payments(
        {"razorpay": object()}, default_provider="razorpay"
    )  # only credits and entitlements are used here
    gw = AiGateway(llm, accounts, flags, pay, rates=Rates(1, 3, 1), default_rpm=3)
    app = create_app(Settings(database_url=DB, cache_url=CACHE))
    app.include_router(build_router(gw), prefix="/api")

    async def seed():
        d = Database(DB)
        async with d.session() as s:
            await accounts.ensure_user(s, OWNER, email="o@example.com", email_verified=True)
            org = await accounts.create_org(s, OWNER, "Acme")
            good, raw_good = await accounts.create_api_key(s, org.id, OWNER, "app", ["chat"])
            _, raw_noscope = await accounts.create_api_key(s, org.id, OWNER, "readonly", [])
            _, raw_embed = await accounts.create_api_key(s, org.id, OWNER, "search", ["embed"])
            await pay.grant_credits(s, org.id, 100, reason="seed", ref="seed")
        await d.dispose()
        return org.id, raw_good, raw_noscope, raw_embed

    org_id, key, noscope, embed_key = asyncio.run(seed())
    with TestClient(app) as c:
        c.llm, c.gw, c.pay, c.flags, c.org, c.key, c.noscope, c.embed_key = (
            llm,
            gw,
            pay,
            flags,
            org_id,
            key,
            noscope,
            embed_key,
        )
        c.headers["Authorization"] = f"Bearer {key}"
        yield c
    asyncio.run(_drop())


BODY = {"model": "auto", "messages": [{"role": "user", "content": "Say hello"}]}


def state(c):
    async def go():
        d = Database(DB)
        async with d.session() as s:
            bal = await c.pay.credit_balance(s, c.org)
            rows = (await s.execute(select(UsageRecord).order_by(UsageRecord.id))).scalars().all()
        await d.dispose()
        return bal, rows

    return asyncio.run(go())


def test_authentication_and_scope(ctx):
    assert (
        ctx.post(
            "/api/v1/chat/completions", json=BODY, headers={"Authorization": "Bearer nope"}
        ).status_code
        == 401
    )
    r = ctx.post(
        "/api/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {ctx.noscope}"}
    )
    assert r.status_code == 403 and r.json()["error"]["type"] == "insufficient_scope"
    assert ctx.llm.chat.calls == []  # nothing reached the model


def test_a_request_is_priced_from_tokens_and_recorded(ctx):
    r = ctx.post("/api/v1/chat/completions", json=BODY)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "hello there"
    assert r.headers["x-credits-charged"] == "4"  # 1000 in * 1/1k + 1000 out * 3/1k
    bal, rows = state(ctx)
    assert bal == 96 and len(rows) == 1
    rec = rows[0]
    assert (
        rec.org_id,
        rec.kind,
        rec.model,
        rec.provider,
        rec.units_in,
        rec.units_out,
        rec.credits,
        rec.status,
    ) == (ctx.org, "ai.chat", "llama-3", "groq", 1000, 1000, 4, "ok")


def test_missing_usage_falls_back_to_an_estimate(ctx):
    ctx.llm.chat.usage = None
    r = ctx.post(
        "/api/v1/chat/completions",
        json={**BODY, "messages": [{"role": "user", "content": "x" * 4000}]},
    )
    assert r.status_code == 200
    _, rows = state(ctx)
    assert rows[0].units_in == 1000 and rows[0].units_out == 3  # ~4 chars per token


def test_no_credits_means_no_model_call(ctx):
    async def drain():
        d = Database(DB)
        async with d.session() as s:
            await ctx.pay.spend_credits(s, ctx.org, 100, reason="drain")
        await d.dispose()

    asyncio.run(drain())
    r = ctx.post("/api/v1/chat/completions", json=BODY)
    assert r.status_code == 402 and r.json()["error"]["type"] == "insufficient_credits"
    assert ctx.llm.chat.calls == []


def test_a_costly_request_takes_only_what_is_left(ctx):
    async def leave_two():
        d = Database(DB)
        async with d.session() as s:
            await ctx.pay.spend_credits(s, ctx.org, 98, reason="drain")
        await d.dispose()

    asyncio.run(leave_two())
    r = ctx.post("/api/v1/chat/completions", json=BODY)  # costs 4, only 2 left
    assert r.status_code == 200 and r.headers["x-credits-charged"] == "2"
    assert state(ctx)[0] == 0
    assert (
        ctx.post("/api/v1/chat/completions", json=BODY).status_code == 402
    )  # the next one is refused


def test_rate_limit_comes_from_the_plan_default(ctx):
    codes = [ctx.post("/api/v1/chat/completions", json=BODY).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]  # default_rpm=3
    assert len(ctx.llm.chat.calls) == 3


def test_kill_switch_stops_everything_at_once(ctx):
    async def kill():
        d = Database(DB)
        async with d.session() as s:
            await ctx.flags.set(
                s,
                "kill:ai",
                FlagUpdate(enabled=True),
                actor=Actor("staff", "superadmin"),
                reason="provider incident",
            )
        await d.dispose()

    asyncio.run(kill())
    r = ctx.post("/api/v1/chat/completions", json=BODY)
    assert (
        r.status_code == 503
        and r.json()["error"]["type"] == "feature_disabled"
        and ctx.llm.chat.calls == []
    )


def test_provider_errors_are_returned_recorded_and_not_charged(ctx):
    ctx.llm.chat.fail = ProviderHttpError("all providers are down", status=502)
    r = ctx.post("/api/v1/chat/completions", json=BODY)
    assert r.status_code == 502 and r.json()["error"]["type"] == "upstream_error"
    bal, rows = state(ctx)
    assert bal == 100 and [x.status for x in rows] == ["error"] and rows[0].credits == 0


def test_moderation_can_block_before_the_model_is_called(ctx):
    async def moderate(messages):
        return (
            "banned phrase" if any("forbidden" in str(m.get("content")) for m in messages) else None
        )

    ctx.gw.moderate = moderate
    r = ctx.post(
        "/api/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "forbidden topic"}]},
    )
    assert r.status_code == 400 and r.json()["error"]["type"] == "content_blocked"
    assert "banned phrase" not in r.text  # the reason is for your records, not the caller
    bal, rows = state(ctx)
    assert bal == 100 and rows[0].status == "blocked" and ctx.llm.chat.calls == []


def test_streaming_is_sent_in_openai_format_and_charged_afterwards(ctx):
    with ctx.stream("POST", "/api/v1/chat/completions", json={**BODY, "stream": True}) as r:
        text_ = "".join(r.iter_text())
    assert r.status_code == 200 and text_.rstrip().endswith("data: [DONE]")
    assert text_.count('"content"') == 3 and "there!!!" in text_
    bal, rows = state(ctx)
    assert (
        rows[0].status == "ok" and rows[0].units_out == 4 and rows[0].credits == 1 and bal == 99
    )  # estimate: 14 chars ~ 4 tokens


def test_streaming_errors_before_the_first_chunk_become_real_status_codes(ctx):
    ctx.llm.chat.fail = ProviderHttpError("quota", status=429)
    r = ctx.post("/api/v1/chat/completions", json={**BODY, "stream": True})
    assert r.status_code == 429 and r.json()["error"]["type"] == "upstream_error"
    assert state(ctx)[0] == 100


def test_models_lists_only_enabled_ones(ctx):
    got = ctx.get("/api/v1/models").json()
    assert got["data"] == [{"id": "llama-3", "object": "model", "owned_by": "groq"}]
    assert ctx.get("/api/v1/models", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_catalog_is_saved_loaded_and_applied(ctx):
    from tsp.ai import LLMClient, ModelCatalog, Status
    from tsp.ai.catalog import ModelResult, ProviderResult
    from tsp.ai.providers.base import BaseProvider
    from tsp_api.ai import load_catalog, save_catalog

    class Stub(BaseProvider):
        platform, name = "stub", "Stub"

        async def chat_completion(self, *a, **k):
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        def stream_chat_completion(self, *a, **k):
            raise NotImplementedError

        async def validate_key(self, k):
            return True

        async def list_models(self, k):
            return [{"id": "good-model"}, {"id": "bad-model"}]

    async def go():
        d = Database(DB)
        c = LLMClient()
        c._router.add_provider("stub", Stub())
        c._router.add_key("stub", "k")
        assert await load_catalog(d) is None  # nothing saved yet
        n = await __import__("tsp_api.ai", fromlist=["refresh_catalog"]).refresh_catalog(
            c, d, free_only=False
        )
        saved = await load_catalog(d)
        fresh = LLMClient()  # a restarted process
        fresh._router.add_provider("stub", Stub())
        fresh._router.add_key("stub", "k")
        applied = fresh.use_catalog(saved)
        await d.dispose()
        return n, saved, applied, [m["model_id"] for m in fresh.get_models()]

    n, saved, applied, ids = asyncio.run(go())
    assert (
        n == 2 and applied == 2 and sorted(ids) == ["bad-model", "good-model"]
    )  # both answered the probe
    assert isinstance(saved, ModelCatalog) and saved.providers["stub"].key_status is Status.OK
    _ = (ModelResult, ProviderResult, save_catalog)


# -- embeddings ---------------------------------------------------------------------------------------

EMBED = {"model": "text-embedding-3-small", "input": ["alpha", "beta"]}


def embed_headers(c):
    return {"Authorization": f"Bearer {c.embed_key}"}


def test_embeddings_need_the_embed_scope(ctx):
    r = ctx.post("/api/v1/embeddings", json=EMBED)  # the default key only has "chat"
    assert r.status_code == 403 and "'embed'" in r.json()["error"]["message"]
    assert (
        ctx.post(
            "/api/v1/embeddings", json=EMBED, headers={"Authorization": "Bearer x"}
        ).status_code
        == 401
    )
    assert ctx.llm.embeddings.calls == []
    # and an embed key cannot chat
    chat = ctx.post("/api/v1/chat/completions", json=BODY, headers=embed_headers(ctx))
    assert chat.status_code == 403


def test_embeddings_are_returned_in_openai_shape_priced_and_metered(ctx):
    r = ctx.post("/api/v1/embeddings", json={**EMBED, "dimensions": 2}, headers=embed_headers(ctx))
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list" and body["model"] == "text-embedding-3-small"
    assert [d["index"] for d in body["data"]] == [0, 1] and body["data"][0]["embedding"] == [
        0.5,
        0.25,
    ]
    assert body["usage"] == {"prompt_tokens": 500, "total_tokens": 500}
    assert ctx.llm.embeddings.calls == [
        ("text-embedding-3-small", ["alpha", "beta"], 2, "document")
    ]
    assert r.headers["x-credits-charged"] == "1"  # 500 tokens * 1/1k, rounded up
    bal, rows = state(ctx)
    rec = rows[0]
    assert bal == 99 and len(rows) == 1
    assert (rec.kind, rec.model, rec.provider, rec.units_in, rec.units_out, rec.status) == (
        "ai.embed",
        "text-embedding-3-small",
        "openai",
        500,
        0,
        "ok",
    )


def test_a_single_string_input_is_accepted_and_missing_usage_is_estimated(ctx):
    ctx.llm.embeddings.tokens = 0
    r = ctx.post(
        "/api/v1/embeddings",
        json={"model": "m", "input": "x" * 4000, "input_type": "query"},
        headers=embed_headers(ctx),
    )
    assert r.status_code == 200 and r.json()["usage"]["total_tokens"] == 1000
    assert ctx.llm.embeddings.calls[0][3] == "query"


def test_embeddings_validate_the_request(ctx):
    h = embed_headers(ctx)
    assert (
        ctx.post("/api/v1/embeddings", json={"input": ["a"]}, headers=h).status_code == 422
    )  # no model
    assert (
        ctx.post("/api/v1/embeddings", json={"model": "m", "input": []}, headers=h).status_code
        == 400
    )
    too_many = {"model": "m", "input": ["a"] * 257}
    assert ctx.post("/api/v1/embeddings", json=too_many, headers=h).status_code == 422
    assert ctx.llm.embeddings.calls == []


def test_embeddings_share_the_kill_switch_credit_check_and_rate_limit(ctx):
    h = embed_headers(ctx)
    codes = [ctx.post("/api/v1/embeddings", json=EMBED, headers=h).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]  # default_rpm=3, shared with chat

    left = state(ctx)[0]

    async def drain():
        d = Database(DB)
        async with d.session() as s:
            await ctx.pay.spend_credits(s, ctx.org, left, reason="drain")
        await d.dispose()

    asyncio.run(drain())
    ctx.llm.embeddings.calls.clear()
    r = ctx.post("/api/v1/embeddings", json=EMBED, headers=h)
    assert r.status_code == 402 and r.json()["error"]["type"] == "insufficient_credits"
    assert ctx.llm.embeddings.calls == []


def test_embedding_failures_are_returned_recorded_and_not_charged(ctx):
    h = embed_headers(ctx)
    ctx.llm.embeddings.fail = ProviderHttpError(
        "no provider serves embedding model 'm'", status=404
    )
    r = ctx.post("/api/v1/embeddings", json=EMBED, headers=h)
    assert r.status_code == 404 and r.json()["error"]["type"] == "upstream_error"
    ctx.llm.embeddings.fail = EmbeddingDimensionError("returned 3 dimensions, expected 4")
    r = ctx.post("/api/v1/embeddings", json=EMBED, headers=h)
    assert r.status_code == 502 and "dimensions" in r.json()["error"]["message"]
    bal, rows = state(ctx)
    assert (
        bal == 100
        and [(x.kind, x.status, x.credits) for x in rows] == [("ai.embed", "error", 0)] * 2
    )
