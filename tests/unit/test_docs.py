"""Focused checks for maintained documentation navigation."""

import re
from pathlib import Path
from runpy import run_path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
DOCS = (
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "docs" / "CAPABILITIES.md",
    ROOT / "docs" / "TRUST.md",
    ROOT / "docs" / "RFC-INDEX.md",
)


def test_rfc_index_is_current() -> None:
    render = run_path(str(ROOT / "scripts" / "generate_rfc_index.py"))["render"]
    ledger = (ROOT / "docs" / "RFC.md").read_text(encoding="utf-8")
    current = (ROOT / "docs" / "RFC-INDEX.md").read_text(encoding="utf-8")
    assert current == render(ledger), "run scripts/generate_rfc_index.py"


def test_new_documentation_internal_links_resolve() -> None:
    failures: list[str] = []
    for document in DOCS:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            relative, _, fragment = target.partition("#")
            destination = (document.parent / unquote(relative)).resolve()
            if not destination.is_file():
                failures.append(f"{document.relative_to(ROOT)} -> {target} (missing file)")
                continue
            if fragment:
                headings = destination.read_text(encoding="utf-8")
                if f"#{fragment}" not in _heading_anchors(headings):
                    failures.append(f"{document.relative_to(ROOT)} -> {target} (missing anchor)")
    assert not failures, "\n".join(failures)


def _heading_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    for heading in re.findall(r"^#{1,6} (.+)$", text, flags=re.MULTILINE):
        value = heading.lower().replace("+", "").replace("`", "")
        value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
        anchors.add("#" + re.sub(r"\s+", "-", value))
    return anchors
