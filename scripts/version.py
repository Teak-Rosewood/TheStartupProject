"""One version for the whole repository (docs/adr/0002-versioning-and-releases.md).

The root VERSION file is the source of truth; every Python package and app pyproject.toml must
carry the same version. The JS packages are private apps, not released, and keep their own.

Usage:
  python3 scripts/version.py check            fail if any manifest disagrees with VERSION
  python3 scripts/version.py set 0.3.0        write VERSION and every manifest, then run `uv lock`
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
PY_VERSION = re.compile(r'^version = "([^"]+)"$', re.M)


def manifests() -> list[Path]:
    return sorted(ROOT.glob("python/packages/*/pyproject.toml")) + sorted(
        ROOT.glob("python/apps/*/pyproject.toml")
    )


def read(path: Path) -> str | None:
    m = PY_VERSION.search(path.read_text())
    return m.group(1) if m else None


DUNDER = ROOT / "python/packages/ai/src/tsp/ai/__init__.py"
DUNDER_RE = re.compile(r'^__version__ = "([^"]+)"', re.M)


def write(path: Path, version: str) -> None:
    path.write_text(PY_VERSION.sub(f'version = "{version}"', path.read_text(), count=1))


def main(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "check":
        want = (ROOT / "VERSION").read_text().strip()
        bad = [(p, v) for p in manifests() if (v := read(p)) != want]
        m = DUNDER_RE.search(DUNDER.read_text())
        if not m or m.group(1) != want:
            bad.append((DUNDER, m.group(1) if m else None))
        for p, v in bad:
            print(f"MISMATCH {p.relative_to(ROOT)}: {v!r}, VERSION says {want!r}")
        if bad:
            print("fix with: python3 scripts/version.py set " + want)
            return 1
        print(f"ok: {len(manifests())} manifests at {want}")
        return 0
    if len(argv) == 2 and argv[0] == "set" and SEMVER.match(argv[1]):
        (ROOT / "VERSION").write_text(argv[1] + "\n")
        for p in manifests():
            write(p, argv[1])
        DUNDER.write_text(DUNDER_RE.sub(f'__version__ = "{argv[1]}"', DUNDER.read_text(), count=1))
        print(f"set {len(manifests())} manifests to {argv[1]}; now run: cd python && uv lock")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
