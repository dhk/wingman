"""Resume format extraction: PDF, DOCX, LaTeX, and Google Docs/Drive URLs.

Everything here is deterministic text extraction — no model call. The
extracted plain text is what gets hashed, stored, and quoted against, so
every downstream evidence check ('quote appears verbatim') operates on the
same text the model saw.

Format notes:
- PDF via pypdf (the one format that genuinely needs a dependency).
- DOCX via the standard library: a .docx is a zip whose word/document.xml
  carries every visible run of text.
- LaTeX via a deterministic flattener: comments and commands are stripped,
  argument text is kept. This loses layout — fine for resume extraction,
  where the model reads flattened text — but it is not a TeX engine;
  exotic macro packages degrade to their visible words.
- Google URLs are the one network path (explicit, user-invoked, https —
  RFC-009 invariants): a docs.google.com document is fetched via its
  plain-text export endpoint; a drive.google.com file link is fetched and
  sniffed as PDF/DOCX/text. Only link-accessible documents work — a
  permission wall comes back as HTML and fails visibly.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

from wingman.application.ingest import IngestError
from wingman.infrastructure.fetch import fetch_url
from wingman.infrastructure.logs import get_logger

_logger = get_logger("application.resume_formats")

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_GOOGLE_DOC_HOSTS = {"docs.google.com"}
_GOOGLE_DRIVE_HOSTS = {"drive.google.com"}


def pdf_text(data: bytes, name: str) -> str:
    """Extract text from PDF bytes, page by page."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PyPdfError, ValueError, KeyError, OSError) as exc:
        raise IngestError(
            f"could not extract text from {name} ({exc}). Nothing was ingested; "
            "if the PDF is a scan, export the resume as text first."
        ) from exc
    text = "\n\n".join(page.strip() for page in pages if page.strip())
    if not text.strip():
        raise IngestError(
            f"{name} contains no extractable text — it is likely a scanned image. "
            "Nothing was ingested; export the resume as text and re-run."
        )
    return text


def docx_text(data: bytes, name: str) -> str:
    """Extract text from DOCX bytes with the standard library (zip + XML)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            document = archive.read("word/document.xml")
        root = ElementTree.fromstring(document)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise IngestError(
            f"could not read {name} as a Word document ({exc}). Nothing was ingested."
        ) from exc
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_WORD_NS}p"):
        runs: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{_WORD_NS}t" and node.text:
                runs.append(node.text)
            elif node.tag in (f"{_WORD_NS}tab",):
                runs.append("\t")
            elif node.tag in (f"{_WORD_NS}br", f"{_WORD_NS}cr"):
                runs.append("\n")
        if runs:
            paragraphs.append("".join(runs))
    text = "\n".join(paragraphs)
    if not text.strip():
        raise IngestError(f"{name} contains no extractable text. Nothing was ingested.")
    return text


def latex_text(source: str, name: str) -> str:
    """Flatten LaTeX source to its visible words (deterministic, not a TeX engine).

    Comments and command tokens are stripped, braced argument text is kept,
    \\item becomes a bullet. Enough for the extraction model to read a
    moderncv/altacv-style resume; layout and macro semantics are not
    reproduced.
    """
    text = source
    # keep only the body when a document environment exists
    match = re.search(r"\\begin\{document\}(.*)\\end\{document\}", text, flags=re.DOTALL)
    if match:
        text = match.group(1)
    # comments: % to end of line, but not escaped \%
    text = re.sub(r"(?<!\\)%.*", "", text)
    # whole-environment markers vanish
    text = re.sub(r"\\(?:begin|end)\{[^}]*\}", "\n", text)
    # bullets keep their shape
    text = re.sub(r"\\item\b", "\n- ", text)
    # forced breaks become newlines
    text = re.sub(r"\\\\(?:\[[^\]]*\])?", "\n", text)
    # command tokens go, their braced arguments' text stays
    text = re.sub(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?", " ", text)
    # unescape the characters LaTeX escapes, then drop structural leftovers
    text = re.sub(r"\\([%&_$#{}])", r"\1", text)
    text = text.replace("~", " ").replace("{", " ").replace("}", " ").replace("$", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    if not text.strip():
        raise IngestError(
            f"{name} flattened to nothing — the LaTeX file has no visible text "
            "outside macros. Nothing was ingested; export the resume as PDF or "
            "text and re-run."
        )
    return text


def sniff_resume_text(data: bytes, name: str) -> str:
    """Bytes of unknown format -> text, by magic numbers (PDF, DOCX zip, else UTF-8)."""
    if data.startswith(b"%PDF"):
        return pdf_text(data, name)
    if data.startswith(b"PK\x03\x04"):
        return docx_text(data, name)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IngestError(
            f"{name} is neither PDF, DOCX, nor UTF-8 text ({exc}). Nothing was ingested."
        ) from exc


def extract_resume_text(path: Path) -> str:
    """Read a resume file in any supported format and return plain text."""
    suffix = path.suffix.lower()
    if suffix in {".pdf", ".docx"}:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise IngestError(
                f"could not read {path} ({exc}). Nothing was ingested; "
                "check the path and re-run 'wingman ingest'."
            ) from exc
        return pdf_text(data, path.name) if suffix == ".pdf" else docx_text(data, path.name)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IngestError(
            f"could not read {path} ({exc}). Nothing was ingested; "
            "check the path and re-run 'wingman ingest'."
        ) from exc
    except UnicodeDecodeError as exc:
        raise IngestError(
            f"{path} is not UTF-8 text ({exc}). Nothing was ingested; supported "
            "formats are Markdown, plain text, PDF, DOCX, and LaTeX."
        ) from exc
    if suffix == ".tex":
        return latex_text(text, path.name)
    return text


def _google_export(url: str) -> tuple[str, str]:
    """Map a Google Docs/Drive URL to its export endpoint; returns (doc_id, export_url)."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise IngestError(f"only https URLs are fetched (RFC-009); got {url!r}.")
    host = parts.hostname or ""
    doc = re.search(r"/(?:document|file)/d/([a-zA-Z0-9_-]+)", parts.path)
    if host in _GOOGLE_DOC_HOSTS and doc:
        doc_id = doc.group(1)
        return doc_id, f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    if host in _GOOGLE_DRIVE_HOSTS and doc:
        doc_id = doc.group(1)
        return doc_id, f"https://drive.google.com/uc?export=download&id={doc_id}"
    raise IngestError(
        "only Google Docs (docs.google.com/document/d/...) and Google Drive file "
        "links (drive.google.com/file/d/...) are supported. For anything else, "
        "download the file and run 'wingman ingest <path>'."
    )


def _looks_like_html(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html"))


def fetch_resume_bytes(url: str, fetcher: Callable[[str], bytes] = fetch_url) -> tuple[str, bytes]:
    """Fetch a resume from a Google Docs/Drive URL; returns (locator_name, raw bytes).

    The one network step in resume ingestion: explicit, user-invoked,
    https-only. A document behind a permission wall comes back as an HTML
    sign-in page and fails visibly instead of ingesting garbage. The raw
    bytes are returned so the caller can archive the original artifact in
    the inbox before extraction.
    """
    doc_id, export_url = _google_export(url)
    data = fetcher(export_url)
    if _looks_like_html(data):
        raise IngestError(
            "Google returned a web page instead of the document — it is probably "
            "not link-accessible. Share it as 'Anyone with the link' (or download "
            "it) and re-run. Nothing was ingested."
        )
    name = f"google-{doc_id[:12]}"
    _logger.info("resume_url doc=%s bytes=%d", name, len(data))
    return name, data


def suffix_for_bytes(data: bytes) -> str:
    """The archive suffix matching sniff_resume_text's dispatch."""
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data.startswith(b"PK\x03\x04"):
        return ".docx"
    return ".txt"
