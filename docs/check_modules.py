"""Fail when a package under python/packages is missing from docs/aws/13-library-modules.md.
Usage: python docs/check_modules.py"""

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
page = (root / "docs/aws/13-library-modules.md").read_text()
missing = [
    d.name
    for d in sorted((root / "python/packages").iterdir())
    if (d / "pyproject.toml").exists() and f"`tsp-{d.name}`" not in page
]
for name in missing:
    print(f"MISSING tsp-{name}: add a row to docs/aws/13-library-modules.md")
if missing:
    sys.exit(1)
print("ok: every package is listed in docs/aws/13-library-modules.md")
