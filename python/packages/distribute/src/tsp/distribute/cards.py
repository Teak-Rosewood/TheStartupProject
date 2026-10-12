"""Share cards: 1200x630 PNGs (the size Facebook, X and LinkedIn crop to) with bundled Noto fonts.
Devanagari needs real text shaping, which Pillow only does with libraqm; ``render_card`` refuses to
draw Devanagari without it rather than produce broken letters."""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, features

from .items import Item

FONTS = Path(__file__).parent / "fonts"
SIZE = (1200, 630)
_DEVANAGARI = re.compile("[ऀ-ॿ]")

BG = (255, 248, 240)
INK = (33, 33, 33)
ACCENT = (200, 70, 20)


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text))


def _font(text: str, size: int, bold: bool) -> ImageFont.FreeTypeFont:
    deva = has_devanagari(text)
    if deva and not features.check("raqm"):
        raise RuntimeError("Devanagari cards need Pillow built with libraqm (text shaping)")
    name = (
        ("NotoSansDevanagari" if deva else "NotoSans") + ("-Bold" if bold else "-Regular") + ".ttf"
    )
    engine = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    return ImageFont.truetype(str(FONTS / name), size, layout_engine=engine)


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int, max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if font.getlength(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and font.getlength(lines[-1] + "…") > width:
            lines[-1] = lines[-1][:-1].rstrip()
        lines[-1] += "…"
    return lines


def render_card(item: Item, lang: str, *, site_name: str = "", default_lang: str = "en") -> bytes:
    """A PNG for the item in ``lang`` (or the closest language it has)."""
    code, v = item.variant(lang, default_lang)
    img = Image.new("RGB", SIZE, BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 24, SIZE[1]], fill=ACCENT)
    pad, width = 80, SIZE[0] - 160
    title_font = _font(v.title, 72, bold=True)
    y = 110
    for line in _wrap(v.title, title_font, width, 3):
        d.text((pad, y), line, font=title_font, fill=INK)
        y += 100
    if v.summary:
        sfont = _font(v.summary, 36, bold=False)
        for line in _wrap(v.summary, sfont, width, 2):
            d.text((pad, y + 10), line, font=sfont, fill=(90, 90, 90))
            y += 52
    foot = " · ".join(x for x in (", ".join(item.authors), site_name) if x)
    if foot:
        d.text((pad, SIZE[1] - 90), foot, font=_font(foot, 34, bold=False), fill=ACCENT)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def card_key(item: Item, lang: str, site_name: str = "") -> str:
    """Storage key that changes whenever the card's content does, so caches never serve stale art."""
    _, v = item.variant(lang)
    digest = hashlib.sha256(
        "|".join([v.title, v.summary, ",".join(item.authors), site_name, lang]).encode()
    ).hexdigest()[:16]
    return f"cards/{item.id}/{lang}/{digest}.png"


async def get_or_render(store: Any, item: Item, lang: str, *, site_name: str = "") -> str:
    """The storage key of the card, rendering and saving it the first time."""
    key = card_key(item, lang, site_name)
    if await store.head(key) is None:
        await store.write(key, render_card(item, lang, site_name=site_name), "image/png")
    return key
