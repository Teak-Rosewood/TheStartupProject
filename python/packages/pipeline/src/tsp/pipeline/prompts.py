from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Prompt, PromptActive

_VAR = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}")


@dataclass(frozen=True)
class PromptVersion:
    key: str
    version: int
    body: str
    model_hint: str | None
    params: dict[str, Any]

    def render(self, variables: dict[str, Any] | None = None) -> str:
        """Replace ``{{name}}`` with the variable; a missing variable is an error, not an empty string."""
        values = variables or {}

        def sub(m: re.Match[str]) -> str:
            name = m.group(1)
            if name not in values:
                raise KeyError(f"prompt {self.key}@{self.version} needs variable {name!r}")
            return str(values[name])

        return _VAR.sub(sub, self.body)


def parse_prompt_file(text: str) -> dict[str, Any]:
    """``---`` front matter (``key``, ``model_hint``, ``params`` as one line of JSON, ``notes``), then the body."""
    if not text.startswith("---"):
        raise ValueError("prompt file must start with --- front matter")
    _, head, body = text.split("---", 2)
    meta: dict[str, Any] = {}
    for line in head.strip().splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    if not meta.get("key"):
        raise ValueError("prompt front matter needs a key")
    meta["params"] = json.loads(meta["params"]) if meta.get("params") else {}
    meta["body"] = body.strip("\n")
    return meta


class PromptRegistry:
    """Versioned prompts. A push creates a new version only when something changed; one version per
    key is active, and workflows load the active one."""

    async def push(
        self,
        s: AsyncSession,
        key: str,
        body: str,
        *,
        model_hint: str | None = None,
        params: dict[str, Any] | None = None,
        created_by: str | None = None,
        notes: str | None = None,
    ) -> tuple[int, bool]:
        """Returns ``(version, created)``. The first version of a key becomes active."""
        params = params or {}
        latest = (
            await s.execute(
                select(Prompt).where(Prompt.key == key).order_by(Prompt.version.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if (
            latest
            and latest.body == body
            and latest.model_hint == model_hint
            and latest.params == params
        ):
            return latest.version, False
        version = (latest.version if latest else 0) + 1
        s.add(
            Prompt(
                key=key,
                version=version,
                body=body,
                model_hint=model_hint,
                params=params,
                created_by=created_by,
                notes=notes,
            )
        )
        if latest is None:
            await s.flush()
            await self.activate(s, key, version)
        return version, True

    async def push_dir(
        self, s: AsyncSession, directory: str | Path, created_by: str | None = None
    ) -> dict[str, tuple[int, bool]]:
        out: dict[str, tuple[int, bool]] = {}
        for path in sorted(Path(directory).glob("*.md")):
            meta = parse_prompt_file(path.read_text())
            out[meta["key"]] = await self.push(
                s,
                meta["key"],
                meta["body"],
                model_hint=meta.get("model_hint") or None,
                params=meta["params"],
                created_by=created_by,
                notes=meta.get("notes") or None,
            )
        return out

    async def activate(self, s: AsyncSession, key: str, version: int) -> None:
        exists = (
            await s.execute(
                select(func.count()).where(Prompt.key == key, Prompt.version == version)
            )
        ).scalar_one()
        if not exists:
            raise LookupError(f"prompt {key}@{version} does not exist")
        await s.execute(
            insert(PromptActive)
            .values(key=key, version=version)
            .on_conflict_do_update(index_elements=["key"], set_={"version": version})
        )

    async def get(self, s: AsyncSession, key: str, version: int | None = None) -> PromptVersion:
        """The active version of ``key``, or a specific one."""
        if version is None:
            active = await s.get(PromptActive, key)
            if active is None:
                raise LookupError(f"prompt {key!r} has no active version")
            version = active.version
        row = await s.get(Prompt, (key, version))
        if row is None:
            raise LookupError(f"prompt {key}@{version} does not exist")
        return PromptVersion(row.key, row.version, row.body, row.model_hint, dict(row.params))
