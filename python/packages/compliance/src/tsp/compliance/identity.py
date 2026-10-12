"""Disable, sign out and delete a user in the identity provider (Cognito)."""

from __future__ import annotations

import re
from typing import Protocol

import aioboto3

_SUB = re.compile(r"^[0-9a-fA-F-]{36}$")


class IdentityProvider(Protocol):
    async def sign_out(self, sub: str) -> None: ...

    async def disable(self, sub: str) -> None: ...

    async def delete(self, sub: str) -> None: ...


class CognitoIdentity:
    def __init__(self, user_pool_id: str, region: str | None = None):
        self.pool = user_pool_id
        self._region = region
        self._session = aioboto3.Session()

    async def _username(self, client: object, sub: str) -> str | None:
        if not _SUB.match(sub):  # the sub goes into a filter expression
            raise ValueError("invalid sub")
        resp = await client.list_users(  # type: ignore[attr-defined]
            UserPoolId=self.pool, Filter=f'sub = "{sub}"', Limit=1
        )
        users = resp.get("Users", [])
        return str(users[0]["Username"]) if users else None  # federated users: "Google_123..."

    async def _call(self, action: str, sub: str) -> None:
        async with self._session.client("cognito-idp", region_name=self._region) as c:
            username = await self._username(c, sub)
            if username is None:
                return  # already gone
            await getattr(c, action)(UserPoolId=self.pool, Username=username)

    async def sign_out(self, sub: str) -> None:
        await self._call("admin_user_global_sign_out", sub)

    async def disable(self, sub: str) -> None:
        await self._call("admin_disable_user", sub)

    async def delete(self, sub: str) -> None:
        await self._call("admin_delete_user", sub)
