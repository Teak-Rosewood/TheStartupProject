"""Translation that keeps your terminology: glossary terms are enforced and checked."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .client import LLMClient
    from .models import ChatCompletionResponse


@dataclass(frozen=True)
class Translation:
    text: str
    missing: list[str] = field(
        default_factory=list
    )  # glossary source terms whose required target is absent
    retried: bool = False

    @property
    def flagged(self) -> bool:
        """True when a glossary term is still missing after the retry: a person should look."""
        return bool(self.missing)


def _missing(source: str, output: str, glossary: dict[str, str]) -> list[str]:
    s, o = source.casefold(), output.casefold()
    return [
        term
        for term, target in glossary.items()
        if term.casefold() in s and target.casefold() not in o
    ]


def _prompt(target_lang: str, glossary: dict[str, str], source_lang: str | None) -> str:
    terms = "\n".join(f'- "{k}" must be translated as "{v}"' for k, v in glossary.items())
    return (
        f"Translate the user's text{f' from {source_lang}' if source_lang else ''} into {target_lang}. "
        "Reply with only the translation, keeping the formatting and meaning.\n"
        + (f"Always use these terms:\n{terms}" if glossary else "")
    )


async def translate(
    client: LLMClient,
    text: str,
    target_lang: str,
    glossary: dict[str, str] | None = None,
    model: str = "auto",
    *,
    source_lang: str | None = None,
    **params: Any,
) -> Translation:
    """Translate ``text``. Every glossary term found in the source must appear in its required form;
    if one is missing the model is asked once to fix it, and what is still missing is reported in
    ``Translation.missing`` (``flagged`` is then True) rather than silently accepted."""
    glossary = glossary or {}
    messages = [
        {"role": "system", "content": _prompt(target_lang, glossary, source_lang)},
        {"role": "user", "content": text},
    ]

    async def ask(msgs: list[dict[str, Any]]) -> str:
        resp = await cast(
            "Awaitable[ChatCompletionResponse]",
            client.chat.create(model=model, messages=msgs, **params),
        )
        return "".join(c.message.content or "" for c in resp.choices).strip()

    out = await ask(messages)
    missing = _missing(text, out, glossary)
    if not missing:
        return Translation(out)
    fix = ", ".join(f'"{t}" as "{glossary[t]}"' for t in missing)
    out2 = await ask(
        [
            *messages,
            {"role": "assistant", "content": out},
            {
                "role": "user",
                "content": f"You did not use the required terms. Translate again, using {fix}.",
            },
        ]
    )
    return Translation(out2, _missing(text, out2, glossary), retried=True)
