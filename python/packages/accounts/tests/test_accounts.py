from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from tsp.accounts import Accounts, ForbiddenError, NotFoundError, OwnershipError, has_role
from tsp.accounts.service import InviteError
from tsp.audit import Actor, AuditLog

A, B, C = "sub-a", "sub-b", "sub-c"


@pytest.fixture
def acc():
    return Accounts()


async def user(acc, s, sub, email=None):
    return await acc.ensure_user(s, sub, email=email, email_verified=bool(email))


def test_role_order():
    assert (
        has_role("owner", "admin")
        and has_role("admin", "admin")
        and not has_role("member", "admin")
    )


async def test_ensure_user_is_idempotent_and_stores_only_verified_email(db, acc):
    async with db.session() as s:
        u = await acc.ensure_user(s, A, email="Ann@Example.com", email_verified=False)
        assert u.email is None
    async with db.session() as s:
        u = await acc.ensure_user(s, A, email="Ann@Example.com", email_verified=True, name="Ann")
        assert (u.email, u.name) == ("ann@example.com", "Ann")
        again = await acc.ensure_user(s, A)
        assert again.sub == u.sub


async def test_org_creation_makes_owner_and_unique_slugs(db, acc):
    async with db.session() as s:
        await user(acc, s, A)
        o1 = await acc.create_org(s, A, "Acme Inc")
        o2 = await acc.create_org(s, A, "Acme Inc")
        assert o1.slug == "acme-inc" and o2.slug.startswith("acme-inc-") and o1.slug != o2.slug
        assert [r for _, r in await acc.list_orgs(s, A)] == ["owner", "owner"]


async def test_non_members_cannot_even_see_an_org(db, acc):
    async with db.session() as s:
        await user(acc, s, A)
        await user(acc, s, B)
        org = await acc.create_org(s, A, "Secret")
        with pytest.raises(NotFoundError):
            await acc.require_membership(s, org.id, B)


async def test_roles_and_last_owner_protection(db, acc):
    async with db.session() as s:
        for sub in (A, B, C):
            await user(acc, s, sub)
        org = await acc.create_org(s, A, "Acme")
        inv, tok = await acc.invite(s, org.id, A, "b@example.com")
    async with db.session() as s:
        await acc.ensure_user(s, B, email="b@example.com", email_verified=True)
        await acc.accept_invite(s, tok, B, "b@example.com")
    async with db.session() as s:
        with pytest.raises(ForbiddenError):  # a plain member cannot change roles
            await acc.set_role(s, org.id, B, A, "member")
        await acc.set_role(s, org.id, A, B, "admin")
        with pytest.raises(ForbiddenError):  # admins cannot create owners
            await acc.set_role(s, org.id, B, B, "owner")
        with pytest.raises(OwnershipError):  # the last owner cannot step down or leave
            await acc.set_role(s, org.id, A, A, "member")
        with pytest.raises(OwnershipError):
            await acc.remove_member(s, org.id, A, A)
        await acc.set_role(s, org.id, A, B, "owner")
        await acc.set_role(s, org.id, A, A, "member")  # now allowed: B is also an owner


async def test_invite_rules(db, acc):
    async with db.session() as s:
        await user(acc, s, A)
        await user(acc, s, B)
        org = await acc.create_org(s, A, "Acme")
        inv, tok = await acc.invite(s, org.id, A, "Bea@Example.com", "admin")
        assert inv.token_hash != tok and tok not in str(
            inv.__dict__.values()
        )  # only a hash is stored
        with pytest.raises(ForbiddenError):
            await acc.invite(s, org.id, A, "x@example.com", "owner")
    async with db.session() as s:
        with pytest.raises(InviteError, match="different email"):
            await acc.accept_invite(s, tok, B, "mallory@example.com")
        with pytest.raises(InviteError):
            await acc.accept_invite(s, "not-a-token", B, "bea@example.com")
        m = await acc.accept_invite(s, tok, B, "bea@example.com")
        assert m.role == "admin"
    async with db.session() as s:
        with pytest.raises(InviteError):  # single use
            await acc.accept_invite(s, tok, B, "bea@example.com")


async def test_expired_and_revoked_invites(db):
    acc = Accounts(invite_ttl=timedelta(seconds=-1))
    async with db.session() as s:
        await user(acc, s, A)
        org = await acc.create_org(s, A, "Acme")
        _, tok = await acc.invite(s, org.id, A, "b@example.com")
    async with db.session() as s:
        with pytest.raises(InviteError):
            await acc.accept_invite(s, tok, B, "b@example.com")
    acc2 = Accounts()
    async with db.session() as s:
        inv, tok2 = await acc2.invite(s, org.id, A, "b@example.com")
        await acc2.revoke_invite(s, org.id, A, inv.id)
    async with db.session() as s:
        with pytest.raises(InviteError):
            await acc2.accept_invite(s, tok2, B, "b@example.com")


async def test_api_keys_are_hashed_scoped_and_revocable(db, acc):
    async with db.session() as s:
        await user(acc, s, A)
        org = await acc.create_org(s, A, "Acme")
        key, raw = await acc.create_api_key(s, org.id, A, "ci", ["chat"])
        assert (
            raw.startswith("tsp_")
            and raw.split("_", 2)[2] not in key.key_hash
            and len(key.key_hash) == 64
        )
    async with db.session() as s:
        info = await acc.verify_api_key(s, raw)
        assert info and info.org_id == org.id and info.scopes == ["chat"]
        assert await acc.verify_api_key(s, raw[:-1] + ("A" if raw[-1] != "A" else "B")) is None
        assert await acc.verify_api_key(s, "tsp_deadbeef_" + "x" * 30) is None
        assert await acc.verify_api_key(s, "garbage") is None
        await acc.revoke_api_key(s, org.id, A, key.id)
    async with db.session() as s:
        assert await acc.verify_api_key(s, raw) is None


async def test_only_admins_manage_keys(db, acc):
    async with db.session() as s:
        await user(acc, s, A)
        await user(acc, s, B)
        org = await acc.create_org(s, A, "Acme")
        _, tok = await acc.invite(s, org.id, A, "b@example.com", "member")
    async with db.session() as s:
        await acc.accept_invite(s, tok, B, "b@example.com")
    async with db.session() as s:
        with pytest.raises(ForbiddenError):
            await acc.create_api_key(s, org.id, B, "nope")


async def test_mutations_are_audited(db, acc):
    async with db.session() as s:
        await user(acc, s, A)
        org = await acc.create_org(s, A, "Acme")
        await acc.create_api_key(s, org.id, A, "k")
        await acc.set_disabled(s, A, True, actor=Actor("admin", "support"), reason="abuse")
    async with db.session() as s:
        actions = (await s.execute(select(AuditLog.action).order_by(AuditLog.id))).scalars().all()
    assert actions == ["org.created", "api_key.created", "user.disabled"]


async def test_compliance_export_and_erase(db, acc):
    registry = SimpleNamespace(exporters={}, erasers={})
    registry.register_exporter = lambda n, f: registry.exporters.__setitem__(n, f)
    registry.register_eraser = lambda n, f: registry.erasers.__setitem__(n, f)
    acc.register_with_compliance(registry)
    async with db.session() as s:
        await user(acc, s, A, "a@example.com")
        await user(acc, s, B, "b@example.com")
        solo = await acc.create_org(s, A, "Solo")
        shared = await acc.create_org(s, B, "Shared")
        _, tok = await acc.invite(s, shared.id, B, "a@example.com")
    async with db.session() as s:
        await acc.accept_invite(s, tok, A, "a@example.com")
        data = await registry.exporters["accounts"](s, A)
        assert {o["name"] for o in data["organizations"]} == {"Solo", "Shared"}
        await registry.erasers["accounts"](s, A)
    async with db.session() as s:
        u = await s.get(__import__("tsp.accounts", fromlist=["User"]).User, A)
        assert u.email is None and u.deleted_at is not None
        assert (
            await s.execute(
                text("select deleted_at is not null from orgs where id = :i"), {"i": solo.id}
            )
        ).scalar_one()
        assert await acc.list_orgs(s, B) == [
            (await s.get(__import__("tsp.accounts", fromlist=["Org"]).Org, shared.id), "owner")
        ]


async def test_cannot_delete_a_sole_owner_who_still_has_a_team(db, acc):
    registry = SimpleNamespace(exporters={}, erasers={})
    registry.register_exporter = lambda n, f: None
    registry.register_eraser = lambda n, f: registry.erasers.__setitem__(n, f)
    acc.register_with_compliance(registry)
    async with db.session() as s:
        await user(acc, s, A)
        await user(acc, s, B, "b@example.com")
        org = await acc.create_org(s, A, "Team")
        _, tok = await acc.invite(s, org.id, A, "b@example.com")
    async with db.session() as s:
        await acc.accept_invite(s, tok, B, "b@example.com")
    async with db.session() as s:
        with pytest.raises(OwnershipError, match="transfer ownership"):
            await registry.erasers["accounts"](s, A)
