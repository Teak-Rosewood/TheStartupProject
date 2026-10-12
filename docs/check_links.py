"""Fail on broken relative links in Markdown files. Usage: python docs/check_links.py"""

import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
bad = []
for md in list(root.glob("*.md")) + list((root / "docs").rglob("*.md")):
    text = re.sub(r"```.*?```", "", md.read_text(), flags=re.S)  # ignore code blocks
    for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#")[0]
        if path and not (md.parent / path).exists():
            bad.append(f"{md.relative_to(root)}: {target}")
for line in bad:
    print("BROKEN", line)
if bad:
    sys.exit(1)
print("ok: all relative links resolve")
