import json

import pytest
from compliance_support import LEGAL_SRC
from tsp.compliance import Manifest
from tsp.compliance.render import RenderError, render_document, render_legal


def test_renders_every_public_document_with_versions(tmp_path):
    manifest, warnings = render_legal(LEGAL_SRC / "company.example.toml", LEGAL_SRC, tmp_path)
    assert set(manifest.documents) == {
        "terms",
        "privacy",
        "cookies",
        "aup",
        "refund",
        "dpa",
        "subprocessors",
        "retention",
    }
    assert [d.id for d in manifest.required()] == ["terms", "privacy"]
    terms = (tmp_path / "terms.md").read_text()
    assert "Acme Technologies Private Limited" in terms and "{{" not in terms
    assert "Template, not legal advice" not in terms  # the banner is for template authors
    assert "(/legal/privacy)" in terms  # cross-links point at published URLs
    assert "incident-response" not in {p.stem for p in tmp_path.glob("*.md")}  # internal only
    assert Manifest.load(tmp_path / "documents.json") == manifest
    assert warnings and "terms" in warnings  # '(verify)' markers remain in the templates


def test_version_changes_with_content_unless_pinned(tmp_path):
    cfg = LEGAL_SRC / "company.example.toml"
    m1, _ = render_legal(cfg, LEGAL_SRC, tmp_path / "a")
    custom = tmp_path / "c.toml"
    custom.write_text(cfg.read_text().replace('"1 January 2027"', '"2 February 2027"'))
    m2, _ = render_legal(custom, LEGAL_SRC, tmp_path / "b")
    assert m1.documents["terms"].version != m2.documents["terms"].version
    pinned = tmp_path / "p.toml"
    pinned.write_text(custom.read_text() + '\n[versions]\nterms = "2026-10-12"\n')
    m3, _ = render_legal(pinned, LEGAL_SRC, tmp_path / "c")
    assert m3.documents["terms"].version == "2026-10-12"


def test_undefined_placeholder_is_an_error():
    with pytest.raises(RenderError, match="nope"):
        render_document("hello {{nope}}", {}, {})


def test_cli_strict_fails_while_markers_remain(tmp_path, capsys):
    from tsp.compliance.cli import main

    args = [
        "render-legal",
        "--config",
        str(LEGAL_SRC / "company.example.toml"),
        "--src",
        str(LEGAL_SRC),
        "--out",
        str(tmp_path),
    ]
    assert main(args) == 0
    assert main([*args, "--strict"]) == 1
    assert (
        json.loads((tmp_path / "documents.json").read_text())["documents"]["privacy"]["url"]
        == "/legal/privacy"
    )
