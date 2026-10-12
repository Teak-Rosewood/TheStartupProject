"""FastAPI routes and dependencies. User and session dependencies are passed in (no tsp.server import)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Membership, User
from .service import (
    Accounts,
    AccountsError,
    ApiKeyInfo,
    ForbiddenError,
    InviteError,
    NotFoundError,
    OwnershipError,
)

_STATUS = {NotFoundError: 404, ForbiddenError: 403, OwnershipError: 409, InviteError: 400}


def to_http(e: AccountsError) -> HTTPException:
    return HTTPException(_STATUS.get(type(e), 400), detail={"type": e.code, "message": str(e)})


class OrgIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class InviteIn(BaseModel):
    email: EmailStr
    role: str = "member"


class AcceptIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class RoleIn(BaseModel):
    role: str


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = []


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@dataclass
class AccountsApi:
    router: APIRouter
    account: Callable[..., Any]  # dependency: the signed-in local User
    org_member: Callable[[str], Callable[..., Any]]  # dependency factory: org_member("admin")
    api_key: Callable[..., Any]  # dependency: ApiKeyInfo from a customer API key


def build(accounts: Accounts, user: Callable[..., Any], session: Callable[..., Any]) -> AccountsApi:
    async def account(claims: Any = Depends(user), s: AsyncSession = Depends(session)) -> User:
        raw = getattr(claims, "raw", {}) or {}
        u = await accounts.ensure_user(
            s,
            claims.sub,
            email=claims.email or raw.get("email"),
            email_verified=raw.get("email_verified") in (True, "true"),
            name=raw.get("name"),
        )
        if u.deleted_at or u.disabled_at:
            raise HTTPException(
                403, detail={"type": "account_disabled", "message": "this account is disabled"}
            )
        return u

    def org_member(minimum: str = "member") -> Callable[..., Any]:
        async def dep(
            org_id: str, u: User = Depends(account), s: AsyncSession = Depends(session)
        ) -> Membership:
            try:
                return await accounts.require_membership(s, org_id, u.sub, minimum)
            except AccountsError as e:
                raise to_http(e) from e

        return dep

    async def api_key(request: Request, s: AsyncSession = Depends(session)) -> ApiKeyInfo:
        auth = request.headers.get("authorization", "")
        raw = (
            auth[7:] if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
        )
        info = await accounts.verify_api_key(s, raw)
        if info is None:
            raise HTTPException(
                401, detail={"type": "invalid_api_key", "message": "invalid or revoked API key"}
            )
        return info

    r = APIRouter()

    def guard(fn: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapped(*a: Any, **kw: Any) -> Any:
            try:
                return await fn(*a, **kw)
            except AccountsError as e:
                raise to_http(e) from e

        wrapped.__name__ = fn.__name__
        wrapped.__signature__ = __import__("inspect").signature(fn)  # type: ignore[attr-defined]
        return wrapped

    @r.get("/me")
    async def me(u: User = Depends(account), s: AsyncSession = Depends(session)) -> dict[str, Any]:
        orgs = await accounts.list_orgs(s, u.sub)
        return {
            "sub": u.sub,
            "email": u.email,
            "name": u.name,
            "organizations": [
                {"id": o.id, "name": o.name, "slug": o.slug, "role": role} for o, role in orgs
            ],
        }

    @r.patch("/me")
    async def update_me(body: ProfileIn, u: User = Depends(account)) -> dict[str, Any]:
        u.name = body.name.strip()
        return {"sub": u.sub, "name": u.name}

    @r.post("/orgs", status_code=201)
    @guard
    async def create_org(
        body: OrgIn,
        request: Request,
        u: User = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        org = await accounts.create_org(s, u.sub, body.name, request=request)
        return {"id": org.id, "name": org.name, "slug": org.slug, "role": "owner"}

    @r.get("/orgs")
    async def list_orgs(
        u: User = Depends(account), s: AsyncSession = Depends(session)
    ) -> list[dict[str, Any]]:
        return [
            {"id": o.id, "name": o.name, "slug": o.slug, "plan": o.plan, "role": role}
            for o, role in await accounts.list_orgs(s, u.sub)
        ]

    @r.get("/orgs/{org_id}/members")
    async def members(
        org_id: str,
        m: Membership = Depends(org_member("member")),  # noqa: B008
        s: AsyncSession = Depends(session),
    ) -> list[dict[str, Any]]:
        return [
            {"sub": mem.user_sub, "role": mem.role, "email": usr.email, "name": usr.name}
            for mem, usr in await accounts.members(s, org_id)
        ]

    @r.patch("/orgs/{org_id}/members/{sub}")
    @guard
    async def change_role(
        org_id: str,
        sub: str,
        body: RoleIn,
        request: Request,
        u: User = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> dict[str, str]:
        await accounts.set_role(s, org_id, u.sub, sub, body.role, request=request)
        return {"sub": sub, "role": body.role}

    @r.delete("/orgs/{org_id}/members/{sub}", status_code=204)
    @guard
    async def remove(
        org_id: str,
        sub: str,
        request: Request,
        u: User = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> None:
        await accounts.remove_member(s, org_id, u.sub, sub, request=request)

    @r.post("/orgs/{org_id}/invites", status_code=201)
    @guard
    async def invite(
        org_id: str,
        body: InviteIn,
        request: Request,
        u: User = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        inv, token = await accounts.invite(s, org_id, u.sub, body.email, body.role, request=request)
        # The token is shown once. Email it to the invitee (tsp.notify) or let the inviter copy the link.
        return {
            "id": inv.id,
            "email": inv.email,
            "role": inv.role,
            "expires_at": inv.expires_at,
            "token": token,
        }

    @r.delete("/orgs/{org_id}/invites/{invite_id}", status_code=204)
    @guard
    async def revoke_invite(
        org_id: str,
        invite_id: str,
        request: Request,
        u: User = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> None:
        await accounts.revoke_invite(s, org_id, u.sub, invite_id, request=request)

    @r.post("/invites/accept")
    @guard
    async def accept(
        body: AcceptIn,
        request: Request,
        u: User = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> dict[str, str]:
        if not u.email:
            raise HTTPException(
                400,
                detail={
                    "type": "email_unknown",
                    "message": "sign in again with an ID token that carries a verified email",
                },
            )
        m = await accounts.accept_invite(s, body.token, u.sub, u.email, request=request)
        return {"org_id": m.org_id, "role": m.role}

    @r.post("/orgs/{org_id}/api-keys", status_code=201)
    @guard
    async def create_key(
        org_id: str,
        body: KeyIn,
        request: Request,
        u: User = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        key, raw = await accounts.create_api_key(
            s, org_id, u.sub, body.name, body.scopes, request=request
        )
        return {"id": key.id, "name": key.name, "scopes": key.scopes, "key": raw}  # shown once

    @r.get("/orgs/{org_id}/api-keys")
    @guard
    async def list_keys(
        org_id: str, u: User = Depends(account), s: AsyncSession = Depends(session)
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": k.id,
                "name": k.name,
                "prefix": k.prefix,
                "scopes": k.scopes,
                "last_used_at": k.last_used_at,
                "revoked_at": k.revoked_at,
            }
            for k in await accounts.list_api_keys(s, org_id, u.sub)
        ]

    @r.delete("/orgs/{org_id}/api-keys/{key_id}", status_code=204)
    @guard
    async def revoke_key(
        org_id: str,
        key_id: str,
        request: Request,
        u: User = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> None:
        await accounts.revoke_api_key(s, org_id, u.sub, key_id, request=request)

    return AccountsApi(r, account, org_member, api_key)
