from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, record

from .models import ApiKey, Invite, Membership, Org, User

ROLES = ("member", "admin", "owner")  # increasing privilege
_KEY = re.compile(r"^tsp_([0-9a-f]{8})_([A-Za-z0-9_-]{20,})$")


class AccountsError(Exception):
    """Base class; ``code`` is a stable machine-readable reason."""

    code = "accounts_error"


class NotFoundError(AccountsError):
    code = "not_found"


class ForbiddenError(AccountsError):
    code = "forbidden"


class OwnershipError(AccountsError):
    code = "ownership"


class InviteError(AccountsError):
    code = "invalid_invite"


def has_role(role: str, minimum: str) -> bool:
    return ROLES.index(role) >= ROLES.index(minimum)


@dataclass(frozen=True)
class ApiKeyInfo:
    id: str
    org_id: str
    name: str
    scopes: list[str]


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "org"


class _Compliance(Protocol):
    def register_exporter(
        self, name: str, fn: Callable[[AsyncSession, str], Awaitable[Any]]
    ) -> None: ...

    def register_eraser(
        self, name: str, fn: Callable[[AsyncSession, str], Awaitable[None]]
    ) -> None: ...


class Accounts:
    """``invite_hook(session, invite, token, org)`` and ``user_created_hook(session, user)`` let the app send
    email without this module knowing about it. Hooks are best effort: a failure is logged, never raised."""

    def __init__(
        self,
        *,
        invite_ttl: timedelta = timedelta(days=7),
        touch_interval: timedelta = timedelta(minutes=5),
        invite_hook: Callable[[AsyncSession, Invite, str, Org], Awaitable[None]] | None = None,
        user_created_hook: Callable[[AsyncSession, User], Awaitable[None]] | None = None,
    ):
        self.invite_ttl = invite_ttl
        self.touch_interval = touch_interval
        self.invite_hook = invite_hook
        self.user_created_hook = user_created_hook

    @staticmethod
    async def _hook(
        session: AsyncSession, fn: Callable[..., Awaitable[None]] | None, *args: Any
    ) -> None:
        if fn is None:
            return
        try:
            async with session.begin_nested():
                await fn(session, *args)
        except Exception:
            logging.getLogger("tsp.accounts").exception("accounts hook failed")

    # -- users -------------------------------------------------------------------------------

    async def ensure_user(
        self,
        session: AsyncSession,
        sub: str,
        *,
        email: str | None = None,
        email_verified: bool = False,
        name: str | None = None,
    ) -> User:
        """Create the local profile on first sight and keep it fresh. Only verified emails are stored."""
        now = _now()
        created = await session.execute(
            insert(User)
            .values(sub=sub, created_at=now)
            .on_conflict_do_nothing(index_elements=["sub"])
            .returning(User.sub)
        )
        is_new = created.first() is not None
        user = (await session.execute(select(User).where(User.sub == sub))).scalar_one()
        if email and email_verified and user.email != email.lower():
            user.email = email.lower()
        if name and user.name != name:
            user.name = name
        if user.last_seen_at is None or now - user.last_seen_at > self.touch_interval:
            user.last_seen_at = now
        if is_new:
            await self._hook(session, self.user_created_hook, user)
        return user

    async def set_disabled(
        self,
        session: AsyncSession,
        sub: str,
        disabled: bool,
        *,
        actor: Actor,
        reason: str,
        request: Any = None,
    ) -> None:
        user = await session.get(User, sub)
        if user is None:
            raise NotFoundError("user")
        user.disabled_at = _now() if disabled else None
        await record(
            session,
            "user.disabled" if disabled else "user.enabled",
            actor=actor,
            target=("user", sub),
            request=request,
            reason=reason,
        )

    # -- organizations -----------------------------------------------------------------------

    async def create_org(
        self, session: AsyncSession, sub: str, name: str, *, request: Any = None
    ) -> Org:
        base = _slug(name)
        slug = base
        while (await session.execute(select(Org.id).where(Org.slug == slug))).first():
            slug = f"{base}-{secrets.token_hex(2)}"
        org = Org(name=name.strip(), slug=slug, created_by=sub)
        session.add(org)
        await session.flush()
        session.add(Membership(org_id=org.id, user_sub=sub, role="owner"))
        await record(
            session,
            "org.created",
            actor=Actor(sub),
            target=("org", org.id),
            request=request,
            name=org.name,
        )
        return org

    async def list_orgs(self, session: AsyncSession, sub: str) -> list[tuple[Org, str]]:
        rows = await session.execute(
            select(Org, Membership.role)
            .join(Membership, Membership.org_id == Org.id)
            .where(Membership.user_sub == sub, Org.deleted_at.is_(None))
            .order_by(Org.created_at)
        )
        return [(o, r) for o, r in rows]

    async def membership(self, session: AsyncSession, org_id: str, sub: str) -> Membership | None:
        return (
            await session.execute(
                select(Membership)
                .join(Org, Org.id == Membership.org_id)
                .where(
                    Membership.org_id == org_id,
                    Membership.user_sub == sub,
                    Org.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def require_membership(
        self, session: AsyncSession, org_id: str, sub: str, minimum: str = "member"
    ) -> Membership:
        m = await self.membership(session, org_id, sub)
        if m is None:
            raise NotFoundError("org")  # do not reveal that the org exists
        if not has_role(m.role, minimum):
            raise ForbiddenError(f"requires {minimum}")
        return m

    async def members(self, session: AsyncSession, org_id: str) -> list[tuple[Membership, User]]:
        rows = await session.execute(
            select(Membership, User)
            .join(User, User.sub == Membership.user_sub)
            .where(Membership.org_id == org_id)
            .order_by(Membership.created_at)
        )
        return [(m, u) for m, u in rows]

    async def _owner_count(self, session: AsyncSession, org_id: str) -> int:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Membership)
                    .where(Membership.org_id == org_id, Membership.role == "owner")
                )
            ).scalar_one()
        )

    async def set_role(
        self,
        session: AsyncSession,
        org_id: str,
        actor_sub: str,
        target_sub: str,
        role: str,
        *,
        request: Any = None,
    ) -> None:
        if role not in ROLES:
            raise ForbiddenError("unknown role")
        actor = await self.require_membership(session, org_id, actor_sub, "admin")
        target = await self.membership(session, org_id, target_sub)
        if target is None:
            raise NotFoundError("member")
        if (role == "owner" or target.role == "owner") and actor.role != "owner":
            raise ForbiddenError("only an owner can change owners")
        if (
            target.role == "owner"
            and role != "owner"
            and await self._owner_count(session, org_id) <= 1
        ):
            raise OwnershipError("an organization needs at least one owner")
        before, target.role = target.role, role
        await record(
            session,
            "org.role_changed",
            actor=Actor(actor_sub),
            target=("org", org_id),
            request=request,
            member=target_sub,
            before=before,
            after=role,
        )

    async def remove_member(
        self,
        session: AsyncSession,
        org_id: str,
        actor_sub: str,
        target_sub: str,
        *,
        request: Any = None,
    ) -> None:
        # Members may remove themselves; otherwise admin+ (and only owners may remove owners).
        actor = await self.require_membership(
            session, org_id, actor_sub, "member" if actor_sub == target_sub else "admin"
        )
        target = await self.membership(session, org_id, target_sub)
        if target is None:
            raise NotFoundError("member")
        if target.role == "owner":
            if actor.role != "owner":
                raise ForbiddenError("only an owner can remove an owner")
            if await self._owner_count(session, org_id) <= 1:
                raise OwnershipError("an organization needs at least one owner")
        await session.delete(target)
        await record(
            session,
            "org.member_removed",
            actor=Actor(actor_sub),
            target=("org", org_id),
            request=request,
            member=target_sub,
        )

    # -- invites -----------------------------------------------------------------------------

    async def invite(
        self,
        session: AsyncSession,
        org_id: str,
        actor_sub: str,
        email: str,
        role: str = "member",
        *,
        request: Any = None,
    ) -> tuple[Invite, str]:
        """Create an invitation. The raw token is returned once; only its hash is stored."""
        if role not in ("member", "admin"):
            raise ForbiddenError(
                "invites can grant member or admin; owners are promoted afterwards"
            )
        await self.require_membership(session, org_id, actor_sub, "admin")
        token = secrets.token_urlsafe(32)
        inv = Invite(
            org_id=org_id,
            email=email.strip().lower(),
            role=role,
            token_hash=_hash(token),
            invited_by=actor_sub,
            expires_at=_now() + self.invite_ttl,
        )
        session.add(inv)
        await session.flush()
        await record(
            session,
            "org.invited",
            actor=Actor(actor_sub),
            target=("org", org_id),
            request=request,
            email_hash=_hash(inv.email)[:12],
            role=role,
        )
        org = await session.get(Org, org_id)
        if org is not None:
            await self._hook(session, self.invite_hook, inv, token, org)
        return inv, token

    async def accept_invite(
        self, session: AsyncSession, token: str, sub: str, email: str | None, *, request: Any = None
    ) -> Membership:
        inv = (
            await session.execute(
                select(Invite).where(Invite.token_hash == _hash(token)).with_for_update()
            )
        ).scalar_one_or_none()
        if inv is None or inv.revoked_at or inv.accepted_at or inv.expires_at < _now():
            raise InviteError("this invitation is invalid or has expired")
        if not email or email.lower() != inv.email:
            raise InviteError("this invitation was sent to a different email address")
        org = await session.get(Org, inv.org_id)
        if org is None or org.deleted_at:
            raise InviteError("this invitation is invalid or has expired")
        existing = await self.membership(session, inv.org_id, sub)
        inv.accepted_at = _now()
        if existing:
            return existing
        m = Membership(org_id=inv.org_id, user_sub=sub, role=inv.role)
        session.add(m)
        await record(
            session,
            "org.invite_accepted",
            actor=Actor(sub),
            target=("org", inv.org_id),
            request=request,
            role=inv.role,
        )
        return m

    async def revoke_invite(
        self,
        session: AsyncSession,
        org_id: str,
        actor_sub: str,
        invite_id: str,
        *,
        request: Any = None,
    ) -> None:
        await self.require_membership(session, org_id, actor_sub, "admin")
        inv = await session.get(Invite, invite_id)
        if inv is None or inv.org_id != org_id:
            raise NotFoundError("invite")
        inv.revoked_at = _now()
        await record(
            session,
            "org.invite_revoked",
            actor=Actor(actor_sub),
            target=("org", org_id),
            request=request,
            invite=invite_id,
        )

    # -- API keys ----------------------------------------------------------------------------

    async def create_api_key(
        self,
        session: AsyncSession,
        org_id: str,
        actor_sub: str,
        name: str,
        scopes: list[str] | None = None,
        *,
        expires_at: datetime | None = None,
        request: Any = None,
    ) -> tuple[ApiKey, str]:
        await self.require_membership(session, org_id, actor_sub, "admin")
        prefix, secret = secrets.token_hex(4), secrets.token_urlsafe(32)
        key = ApiKey(
            org_id=org_id,
            name=name.strip(),
            prefix=prefix,
            key_hash=_hash(secret),
            scopes=list(scopes or []),
            created_by=actor_sub,
            expires_at=expires_at,
        )
        session.add(key)
        await session.flush()
        await record(
            session,
            "api_key.created",
            actor=Actor(actor_sub),
            target=("org", org_id),
            request=request,
            key=key.id,
            name=key.name,
        )
        return key, f"tsp_{prefix}_{secret}"

    async def list_api_keys(
        self, session: AsyncSession, org_id: str, actor_sub: str
    ) -> list[ApiKey]:
        await self.require_membership(session, org_id, actor_sub, "admin")
        return list(
            (
                await session.execute(
                    select(ApiKey).where(ApiKey.org_id == org_id).order_by(ApiKey.created_at)
                )
            ).scalars()
        )

    async def revoke_api_key(
        self,
        session: AsyncSession,
        org_id: str,
        actor_sub: str,
        key_id: str,
        *,
        request: Any = None,
    ) -> None:
        await self.require_membership(session, org_id, actor_sub, "admin")
        key = await session.get(ApiKey, key_id)
        if key is None or key.org_id != org_id:
            raise NotFoundError("api key")
        key.revoked_at = _now()
        await record(
            session,
            "api_key.revoked",
            actor=Actor(actor_sub),
            target=("org", org_id),
            request=request,
            key=key_id,
        )

    async def verify_api_key(self, session: AsyncSession, raw: str) -> ApiKeyInfo | None:
        m = _KEY.match(raw or "")
        if not m:
            return None
        prefix, secret = m.groups()
        key = (
            await session.execute(select(ApiKey).where(ApiKey.prefix == prefix))
        ).scalar_one_or_none()
        # Compare even when the key is unknown so timing does not reveal which prefixes exist.
        expected = key.key_hash if key else _hash("x")
        if not hmac.compare_digest(expected, _hash(secret)) or key is None:
            return None
        now = _now()
        if key.revoked_at or (key.expires_at and key.expires_at < now):
            return None
        org = await session.get(Org, key.org_id)
        if org is None or org.deleted_at:
            return None
        if key.last_used_at is None or now - key.last_used_at > self.touch_interval:
            key.last_used_at = now
        return ApiKeyInfo(key.id, key.org_id, key.name, list(key.scopes or []))

    # -- compliance --------------------------------------------------------------------------

    def register_with_compliance(self, compliance: _Compliance) -> None:
        compliance.register_exporter("accounts", self._export)
        compliance.register_eraser("accounts", self._erase)

    async def _export(self, session: AsyncSession, sub: str) -> dict[str, Any]:
        user = await session.get(User, sub)
        orgs = await self.list_orgs(session, sub)
        keys = (
            (await session.execute(select(ApiKey).where(ApiKey.created_by == sub))).scalars().all()
        )
        return {
            "profile": None
            if user is None
            else {"email": user.email, "name": user.name, "created_at": user.created_at},
            "organizations": [{"id": o.id, "name": o.name, "role": r} for o, r in orgs],
            "api_keys_created": [
                {"id": k.id, "name": k.name, "created_at": k.created_at, "revoked_at": k.revoked_at}
                for k in keys
            ],
        }

    async def _erase(self, session: AsyncSession, sub: str) -> None:
        for org, _ in await self.list_orgs(session, sub):
            m = await self.membership(session, org.id, sub)
            if m is None:
                continue
            others = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(Membership)
                        .where(Membership.org_id == org.id, Membership.user_sub != sub)
                    )
                ).scalar_one()
            )
            if m.role == "owner" and await self._owner_count(session, org.id) <= 1:
                if others:
                    raise OwnershipError(
                        f"transfer ownership of organization {org.id} before deleting this account"
                    )
                org.deleted_at = _now()  # sole member: the organization goes with them
                await session.execute(
                    update(ApiKey).where(ApiKey.org_id == org.id).values(revoked_at=_now())
                )
        user = await session.get(User, sub)
        if user is not None:
            await session.execute(delete(Invite).where(Invite.email == (user.email or "\0")))
            await session.execute(delete(Membership).where(Membership.user_sub == sub))
            user.email, user.name, user.deleted_at, user.attribution = None, None, _now(), {}
