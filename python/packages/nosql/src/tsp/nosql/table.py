"""Single-table helper: items carry ``pk`` / ``sk`` strings; numbers come back as int/float."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import aioboto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


class ItemExists(Exception):
    """A conditional put found an existing item."""


@dataclass
class Page:
    items: list[dict[str, Any]]
    cursor: str | None  # opaque; pass back to continue


def expires_in(seconds: int) -> int:
    """Epoch seconds for the table's TTL attribute (``expires_at``)."""
    return int(time.time()) + seconds


def _out(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _out(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_out(v) for v in value]
    return value


def _in(value: Any) -> Any:
    """DynamoDB rejects float; store as Decimal."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _in(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_in(v) for v in value]
    return value


class Table:
    def __init__(self, name: str, *, region: str | None = None, endpoint_url: str | None = None):
        self.name = name
        self._session = aioboto3.Session()
        self._region = region
        self._endpoint = endpoint_url  # set for DynamoDB Local in tests

    @asynccontextmanager
    async def _table(self) -> AsyncIterator[Any]:
        async with self._session.resource(
            "dynamodb", region_name=self._region, endpoint_url=self._endpoint
        ) as ddb:
            yield await ddb.Table(self.name)

    async def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        async with self._table() as t:
            res = await t.get_item(Key={"pk": pk, "sk": sk})
        item = res.get("Item")
        return _out(item) if item else None

    async def put(self, item: dict[str, Any], *, if_not_exists: bool = False) -> None:
        kwargs: dict[str, Any] = {"Item": _in(item)}
        if if_not_exists:
            kwargs["ConditionExpression"] = "attribute_not_exists(pk)"
        async with self._table() as t:
            try:
                await t.put_item(**kwargs)
            except ClientError as e:
                if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    raise ItemExists(f"{item.get('pk')}/{item.get('sk')}") from e
                raise

    async def update(self, pk: str, sk: str, values: dict[str, Any]) -> dict[str, Any]:
        """Set attributes; creates the item if absent. Returns the new item."""
        names = {f"#a{i}": k for i, k in enumerate(values)}
        vals = {f":v{i}": _in(v) for i, v in enumerate(values.values())}
        expr = "SET " + ", ".join(f"#a{i} = :v{i}" for i in range(len(values)))
        async with self._table() as t:
            res = await t.update_item(
                Key={"pk": pk, "sk": sk},
                UpdateExpression=expr,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=vals,
                ReturnValues="ALL_NEW",
            )
        return _out(res["Attributes"])

    async def delete(self, pk: str, sk: str) -> None:
        async with self._table() as t:
            await t.delete_item(Key={"pk": pk, "sk": sk})

    async def query(
        self,
        pk: str,
        *,
        sk_prefix: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        reverse: bool = False,
    ) -> Page:
        cond = Key("pk").eq(pk)
        if sk_prefix:
            cond = cond & Key("sk").begins_with(sk_prefix)
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": cond,
            "Limit": limit,
            "ScanIndexForward": not reverse,
        }
        if cursor:
            kwargs["ExclusiveStartKey"] = json.loads(base64.urlsafe_b64decode(cursor))
        async with self._table() as t:
            res = await t.query(**kwargs)
        last = res.get("LastEvaluatedKey")
        next_cursor = base64.urlsafe_b64encode(json.dumps(last).encode()).decode() if last else None
        return Page(_out(res.get("Items", [])), next_cursor)

    async def batch_put(self, items: list[dict[str, Any]]) -> None:
        async with self._table() as t:
            async with t.batch_writer() as w:
                for item in items:
                    await w.put_item(Item=_in(item))

    async def ping(self) -> bool:
        async with self._session.client(
            "dynamodb", region_name=self._region, endpoint_url=self._endpoint
        ) as c:
            await c.describe_table(TableName=self.name)
        return True
