"""Fail if a template uses a {{placeholder}} that company.example.toml does not define.

Usage: python docs/legal/check_placeholders.py
"""

import re
import sys
import tomllib
from pathlib import Path

here = Path(__file__).parent
defined = set(tomllib.loads((here / "company.example.toml").read_text()))
used: dict[str, set[str]] = {}
for md in sorted(here.glob("*.md")):
    for name in re.findall(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}", md.read_text()):
        used.setdefault(name, set()).add(md.name)

missing = {k: sorted(v) for k, v in used.items() if k not in defined}
unused = sorted(defined - set(used))
for k, files in sorted(missing.items()):
    print(f"UNDEFINED {{{{{k}}}}} used in {', '.join(files)}")
for k in unused:
    print(f"note: {k} is defined but never used")
if missing:
    sys.exit(1)
print(f"ok: {len(used)} placeholders, all defined")
