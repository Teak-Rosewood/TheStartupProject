import os
import uuid

import aioboto3
import pytest
from tsp.nosql import ItemExists, Table, expires_in

ENDPOINT = os.environ.get("TSP_TEST_DYNAMO_URL", "http://127.0.0.1:58000")


@pytest.fixture
async def table(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    name = f"t{uuid.uuid4().hex[:8]}"
    async with aioboto3.Session().client(
        "dynamodb", region_name="us-east-1", endpoint_url=ENDPOINT
    ) as c:
        try:
            await c.create_table(
                TableName=name,
                BillingMode="PAY_PER_REQUEST",
                AttributeDefinitions=[
                    {"AttributeName": "pk", "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"},
                ],
                KeySchema=[
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
            )
        except Exception:
            pytest.skip("no DynamoDB Local at TSP_TEST_DYNAMO_URL")
        yield Table(name, region="us-east-1", endpoint_url=ENDPOINT)
        await c.delete_table(TableName=name)


async def test_put_get_numbers_roundtrip(table):
    await table.put({"pk": "USER#1", "sk": "PROFILE", "age": 30, "score": 1.5, "tags": ["a"]})
    assert await table.get("USER#1", "PROFILE") == {
        "pk": "USER#1",
        "sk": "PROFILE",
        "age": 30,
        "score": 1.5,
        "tags": ["a"],
    }
    assert await table.get("USER#1", "NOPE") is None


async def test_conditional_put(table):
    await table.put({"pk": "A", "sk": "1"}, if_not_exists=True)
    with pytest.raises(ItemExists):
        await table.put({"pk": "A", "sk": "1"}, if_not_exists=True)


async def test_update_creates_and_merges(table):
    out = await table.update("U", "S", {"n": 1})
    assert out["n"] == 1
    out = await table.update("U", "S", {"m": "x"})
    assert out["n"] == 1 and out["m"] == "x"


async def test_query_prefix_and_paging(table):
    await table.batch_put(
        [{"pk": "O", "sk": f"ORDER#{i:03d}", "expires_at": expires_in(60)} for i in range(5)]
    )
    await table.put({"pk": "O", "sk": "PROFILE"})
    page = await table.query("O", sk_prefix="ORDER#", limit=2)
    assert [i["sk"] for i in page.items] == ["ORDER#000", "ORDER#001"] and page.cursor
    page2 = await table.query("O", sk_prefix="ORDER#", limit=10, cursor=page.cursor)
    assert [i["sk"] for i in page2.items] == ["ORDER#002", "ORDER#003", "ORDER#004"]
    assert page2.cursor is None


async def test_delete(table):
    await table.put({"pk": "D", "sk": "1"})
    await table.delete("D", "1")
    assert await table.get("D", "1") is None
