"""Message catalogs from gettext ``.po`` files (``<dir>/<lang>/messages.po``), read directly with
Babel, so there is no compile step. Messages use ``{name}`` placeholders."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from babel.messages.pofile import read_po

log = logging.getLogger("tsp.i18n")
BUILTIN = Path(__file__).parent / "locales"


class Catalog:
    """``t("key", "hi", count=3)``. A key missing in a language falls back to the default language,
    then to the key itself (visible in the page, and logged once), never to an error page."""

    def __init__(self, *dirs: str | Path, default: str = "en"):
        self.default = default
        self._msgs: dict[str, dict[str, str]] = {}
        self._warned: set[tuple[str, str]] = set()
        for d in (BUILTIN, *map(Path, dirs)):  # later directories override earlier ones
            self._load(d)

    def _load(self, root: Path) -> None:
        for po in sorted(root.glob("*/messages.po")):
            lang = po.parent.name
            with po.open("rb") as f:
                cat = read_po(f, locale=None)
            bucket = self._msgs.setdefault(lang, {})
            for m in cat:
                if m.id and isinstance(m.id, str) and m.string and isinstance(m.string, str):
                    bucket[m.id] = m.string

    @property
    def languages(self) -> list[str]:
        return sorted(self._msgs)

    def has(self, key: str, lang: str) -> bool:
        return key in self._msgs.get(lang, {})

    def t(self, key: str, lang: str | None = None, **vars: Any) -> str:
        lang = (lang or self.default).split("-")[0].lower()
        text = self._msgs.get(lang, {}).get(key)
        if text is None:
            if (key, lang) not in self._warned:
                self._warned.add((key, lang))
                log.warning("missing translation: %s [%s]", key, lang)
            text = self._msgs.get(self.default, {}).get(key, key)
        try:
            return text.format_map(vars)
        except (KeyError, IndexError, ValueError):
            return text  # a bad placeholder must not take a page down

    def missing(self, lang: str) -> list[str]:
        """Keys the default language has that ``lang`` lacks (for a translation checklist)."""
        base = self._msgs.get(self.default, {})
        have = self._msgs.get(lang, {})
        return sorted(k for k in base if k not in have)


def default_catalog(*dirs: str | Path) -> Catalog:
    return Catalog(*dirs)
