"""Print the CHANGELOG.md section for one version (the text under `## X.Y.Z ...`).
Usage: python3 scripts/changelog_section.py 0.2.0"""

import re
import sys
from pathlib import Path

text = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text()
version = sys.argv[1]
m = re.search(rf"^## \[?{re.escape(version)}\]?[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
print(m.group(1).strip() if m else "", end="")
