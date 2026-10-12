"""Alias normalisation shared by writes and lookups."""

from __future__ import annotations

import re
import unicodedata

_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace: ``"  Café  Müller "`` -> ``"cafe muller"``.

    Accents are removed only from Latin, Greek and Cyrillic letters. In scripts such as Devanagari the
    combining marks are vowel signs, so they stay.
    """
    out: list[str] = []
    base_is_alphabetic = False
    for c in unicodedata.normalize("NFKD", text.casefold()):
        if unicodedata.category(c) == "Mn":
            if not base_is_alphabetic:
                out.append(c)
            continue
        base_is_alphabetic = unicodedata.name(c, "").startswith(("LATIN", "GREEK", "CYRILLIC"))
        out.append(c)
    return _SPACES.sub(" ", unicodedata.normalize("NFC", "".join(out))).strip()
