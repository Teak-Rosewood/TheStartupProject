"""Demo endpoints that exercise every backing service: auth, Postgres, DynamoDB, Valkey."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.auth import Claims
from tsp.cache import Cache
from tsp.nosql import Table
from tsp.server import current_user, db_session, get_cache, get_table

from .models import Note

router = APIRouter()
# Routes that need accepted legal documents; main.py attaches the consent guard.
gated = APIRouter()


async def export_notes(session: AsyncSession, sub: str) -> list[dict[str, Any]]:
    rows = (await session.scalars(select(Note).where(Note.owner == sub).order_by(Note.id))).all()
    return [{"id": n.id, "text": n.text, "created_at": n.created_at} for n in rows]


async def erase_notes(session: AsyncSession, sub: str) -> None:
    await session.execute(delete(Note).where(Note.owner == sub))


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class KvIn(BaseModel):
    value: Any


@router.get("/me")
async def me(user: Claims = Depends(current_user)) -> dict[str, Any]:
    return {"sub": user.sub, "email": user.email, "groups": user.groups}


@gated.post("/notes", status_code=201)
async def add_note(
    body: NoteIn, user: Claims = Depends(current_user), s: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    note = Note(owner=user.sub, text=body.text)
    s.add(note)
    await s.flush()
    return {"id": note.id, "text": note.text}


@router.get("/notes")
async def list_notes(
    user: Claims = Depends(current_user), s: AsyncSession = Depends(db_session)
) -> list[dict[str, Any]]:
    rows = (
        await s.scalars(
            select(Note).where(Note.owner == user.sub).order_by(Note.id.desc()).limit(50)
        )
    ).all()
    return [{"id": n.id, "text": n.text} for n in rows]


@router.put("/kv/{key}")
async def put_kv(
    key: str, body: KvIn, user: Claims = Depends(current_user), t: Table = Depends(get_table)
) -> dict[str, str]:
    await t.put({"pk": f"USER#{user.sub}", "sk": f"KV#{key}", "value": body.value})
    return {"key": key}


@router.get("/kv/{key}")
async def get_kv(
    key: str, user: Claims = Depends(current_user), t: Table = Depends(get_table)
) -> dict[str, Any]:
    from fastapi import HTTPException

    item = await t.get(f"USER#{user.sub}", f"KV#{key}")
    if item is None:
        raise HTTPException(404, "not found")
    return {"key": key, "value": item["value"]}


@router.get("/hits")
async def hits(c: Cache = Depends(get_cache)) -> dict[str, int]:
    return {"hits": await c.incr("hits")}
