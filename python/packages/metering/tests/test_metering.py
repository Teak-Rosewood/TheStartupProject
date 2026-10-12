import asyncio
import os

import pytest
from sqlalchemy import text
from tsp.db import Database, upgrade
from tsp.metering import Rates, estimate_tokens, price_credits, record_usage, summary

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")


def test_pricing_rounds_up_and_has_a_minimum():
    r = Rates(in_per_1k=1, out_per_1k=3, min_charge=1)
    assert price_credits(0, 0, r) == 1  # every successful call costs something
    assert price_credits(1000, 0, r) == 1 and price_credits(1001, 0, r) == 2  # fractions round up
    assert price_credits(500, 500, r) == 2 and price_credits(2000, 1000, r) == 5
    assert price_credits(10, 0, Rates(1, 3, min_charge=0)) == 1  # 0.01 rounds up to 1, not 0
    assert price_credits(0, 0, Rates(1, 3, min_charge=0)) == 0
    with pytest.raises(ValueError):
        Rates(in_per_1k=-1)


def test_token_estimate():
    assert (
        estimate_tokens("") == 0
        and estimate_tokens("hi") == 1
        and estimate_tokens("x" * 400) == 100
    )


@pytest.fixture
async def db(tmp_path):
    d = Database(URL)
    try:
        await d.ping()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    async with d.session() as s:
        await s.execute(text("DROP TABLE IF EXISTS usage_records, alembic_version CASCADE"))
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.metering"])
    yield d
    async with d.session() as s:
        await s.execute(text("DROP TABLE IF EXISTS usage_records, alembic_version CASCADE"))
    await d.dispose()


async def test_summary_counts_only_successful_calls_of_one_org(db):
    async with db.session() as s:
        for org, model, tin, tout, credits, status in (
            ("o1", "llama", 100, 200, 3, "ok"),
            ("o1", "llama", 50, 50, 1, "ok"),
            ("o1", "gpt", 10, 10, 1, "ok"),
            ("o1", "gpt", 0, 0, 0, "error"),
            ("o1", "gpt", 0, 0, 0, "blocked"),
            ("o2", "llama", 999, 999, 9, "ok"),
        ):
            await record_usage(
                s,
                org_id=org,
                kind="ai.chat",
                model=model,
                units_in=tin,
                units_out=tout,
                credits=credits,
                status=status,
            )
    async with db.session() as s:
        got = await summary(s, "o1", 30)
    assert (got["requests"], got["tokens_in"], got["tokens_out"], got["credits"]) == (
        3,
        160,
        260,
        5,
    )
    assert got["by_model"][0] == {"model": "llama", "requests": 2, "credits": 4}
    assert len(got["by_day"]) == 1 and got["by_day"][0]["requests"] == 3
