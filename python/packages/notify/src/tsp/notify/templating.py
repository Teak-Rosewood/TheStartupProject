from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined, select_autoescape

_DIR = Path(__file__).parent / "templates"


@dataclass
class Rendered:
    subject: str
    html: str
    text: str


def _to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</h\d>", "\n", html)
    html = re.sub(r"(?is)<a [^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", r"\2 (\1)", html)
    text = re.sub(r"<[^>]+>", "", html)
    text = (
        re.sub(r"&nbsp;", " ", text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    )
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip() + "\n"


class Templates:
    """Jinja templates with a subject block. Missing variables are errors, not blank spots in a customer's inbox."""

    def __init__(self, *extra_dirs: str | Path):
        loaders: list[Any] = [FileSystemLoader(str(d)) for d in extra_dirs] + [
            FileSystemLoader(str(_DIR))
        ]
        self.env = Environment(
            loader=ChoiceLoader(loaders),
            autoescape=select_autoescape(["html"]),
            undefined=StrictUndefined,
        )

    def render(self, name: str, ctx: dict[str, Any]) -> Rendered:
        tpl = self.env.get_template(f"{name}.html")
        context = tpl.new_context(ctx)
        subject = "".join(tpl.blocks["subject"](context)).strip()
        html = tpl.render(ctx)
        return Rendered(" ".join(subject.split()), html, _to_text(html))
