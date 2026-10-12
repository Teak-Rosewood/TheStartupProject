"""Language handling for full-text search. Postgres has no Hindi stemmer, so Hindi uses the
``simple`` configuration after normalising the spelling variants people actually type."""

from __future__ import annotations

import re

# U+0958..U+095F are precomposed consonant+nukta letters; map them to the plain consonant.
_PRECOMPOSED = str.maketrans(
    "\u0958\u0959\u095a\u095b\u095c\u095d\u095e\u095f",
    "\u0915\u0916\u0917\u091c\u0921\u0922\u092b\u092f",
)
_NUKTA = "़"
_JOINERS = re.compile("[‌‍]")  # zero-width non-joiner / joiner
_CHANDRABINDU = "ँ"
_ANUSVARA = "ं"
_DEVANAGARI = re.compile("[ऀ-ॿ]")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def normalize_devanagari(text: str) -> str:
    """Make spelling variants of the same word compare equal: nukta (with or without precomposed
    letters), zero-width joiners, and chandrabindu versus anusvara. Mirrors the SQL function
    ``tsp_norm_devanagari`` the search index uses."""
    text = _JOINERS.sub("", text)
    text = text.translate(_PRECOMPOSED).replace(_NUKTA, "")
    return text.replace(_CHANDRABINDU, _ANUSVARA)


def detect_lang(text: str) -> str:
    """``hi`` when Devanagari makes up a third or more of the letters, else ``en``. A script check,
    not a language identifier: Marathi and Nepali, also Devanagari, are indexed as Hindi (``simple``)."""
    letters = _LETTER.findall(text)
    if not letters:
        return "en"
    return "hi" if len(_DEVANAGARI.findall(text)) / len(letters) >= 0.34 else "en"
