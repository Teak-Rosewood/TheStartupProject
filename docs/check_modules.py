"""Fail when a package under python/packages is missing from docs/aws/13-library-modules.md or
from the module table in README.md.
Usage: python docs/check_modules.py"""

import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
pages = {
    "docs/aws/13-library-modules.md": (root / "docs/aws/13-library-modules.md").read_text(),
    "README.md": (root / "README.md").read_text(),
}
packages = [d.name for d in sorted((root / "python/packages").iterdir()) if (d / "pyproject.toml").exists()]
failed = False
for page, text in pages.items():
    for name in packages:
        # README rows list names with or without backticks; match the whole name either way.
        if not re.search(rf"(?<![\w-])tsp-{re.escape(name)}(?![\w-])", text):
            print(f"MISSING tsp-{name}: add it to {page}")
            failed = True
if failed:
    sys.exit(1)
print("ok: every package is listed in docs/aws/13-library-modules.md and README.md")
