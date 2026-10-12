"""Fill the legal templates from a company config and write the manifest the API and site use."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from .documents import Document, Manifest

# template file -> (document id, title, requires consent). incident-response is internal: never published.
PUBLIC_DOCUMENTS: dict[str, tuple[str, str, bool]] = {
    "terms-of-service.md": ("terms", "Terms of Service", True),
    "privacy-policy.md": ("privacy", "Privacy Policy", True),
    "cookie-policy.md": ("cookies", "Cookie Policy", False),
    "acceptable-use.md": ("aup", "Acceptable Use Policy", False),
    "refund-cancellation.md": ("refund", "Refund and Cancellation Policy", False),
    "dpa.md": ("dpa", "Data Processing Agreement", False),
    "subprocessors.md": ("subprocessors", "Sub-processors", False),
    "data-retention.md": ("retention", "Data Retention Schedule", False),
}

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
_BANNER = re.compile(r"^> \*\*Template, not legal advice\.\*\*.*\n+", re.M)
_COMMENT = re.compile(r"<!--.*?-->\n?", re.S)


class RenderError(Exception):
    pass


def _link_map(url_prefix: str) -> dict[str, str]:
    return {
        f: f"{url_prefix.rstrip('/')}/{doc_id}" for f, (doc_id, _, _) in PUBLIC_DOCUMENTS.items()
    }


def render_document(
    text: str, values: dict[str, str], links: dict[str, str]
) -> tuple[str, list[str]]:
    missing = sorted({m for m in _PLACEHOLDER.findall(text) if m not in values})
    if missing:
        raise RenderError(f"undefined placeholders: {', '.join(missing)}")
    out = _PLACEHOLDER.sub(lambda m: values[m.group(1)], text)
    out = _COMMENT.sub("", _BANNER.sub("", out))
    for filename, url in links.items():
        out = out.replace(f"]({filename})", f"]({url})")
    warnings = []
    if "(verify)" in out or "**(verify" in out:
        warnings.append("contains '(verify)' markers: confirm with counsel and remove them")
    if "*(Optional" in out or "(optional)" in out.lower():
        warnings.append("contains optional sections: keep or delete them")
    return out.strip() + "\n", warnings


def render_legal(
    config_path: str | Path,
    src: str | Path,
    out: str | Path,
    *,
    url_prefix: str = "/legal",
) -> tuple[Manifest, dict[str, list[str]]]:
    """Render every public document into ``out`` and write ``documents.json``.

    A version defaults to the first 12 hex digits of the rendered text's SHA-256, so any edit asks
    users to accept again. Pin a version in the config's ``[versions]`` table to keep it across
    editorial fixes. Returns the manifest and per-document warnings.
    """
    config = tomllib.loads(Path(config_path).read_text())
    pinned = config.get("versions", {})
    values = {k: str(v) for k, v in config.items() if isinstance(v, (str, int, float))}
    default_consent = [doc_id for doc_id, _, needs in PUBLIC_DOCUMENTS.values() if needs]
    consent_ids = set(config.get("consent_documents", default_consent))
    links = _link_map(url_prefix)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)

    docs: dict[str, Document] = {}
    warnings: dict[str, list[str]] = {}
    for filename, (doc_id, title, _) in PUBLIC_DOCUMENTS.items():
        rendered, warn = render_document((Path(src) / filename).read_text(), values, links)
        digest = hashlib.sha256(rendered.encode()).hexdigest()
        (out_dir / f"{doc_id}.md").write_text(rendered)
        docs[doc_id] = Document(
            id=doc_id,
            title=title,
            version=str(pinned.get(doc_id, digest[:12])),
            sha256=digest,
            url=f"{url_prefix.rstrip('/')}/{doc_id}",
            requires_consent=doc_id in consent_ids,
        )
        if warn:
            warnings[doc_id] = warn
    manifest = Manifest(documents=docs)
    (out_dir / "documents.json").write_text(json.dumps(manifest.model_dump(), indent=2) + "\n")
    return manifest, warnings
