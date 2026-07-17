"""Corpus ingestion and evidence search: the user's writing as citable sources.

Entirely deterministic — no model calls. Files (Markdown, plain text, HTML,
or a Substack-style export zip of HTML posts) become immutable SourceRecords
plus CorpusDocuments indexed for full-text search (RFC-007).
"""

from __future__ import annotations

import hashlib
import zipfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from pydantic import BaseModel, Field

from wingman.application.ingest import IngestError
from wingman.domain import SourceRecord
from wingman.domain.corpus import CorpusDocument, EvidenceHit
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.corpus")

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".html", ".htm"}
_SKIPPED_HTML_TAGS = {"script", "style", "head", "nav", "footer"}
_BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr"}


class FileFailure(BaseModel):
    name: str
    reason: str


class CorpusAddReport(BaseModel):
    added: int
    skipped_duplicates: int
    skipped_unsupported: list[str] = Field(default_factory=list)
    failures: list[FileFailure] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)


class _TextExtractor(HTMLParser):
    """Deterministic HTML-to-text: body text plus a title from <title> or first <h1>."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._in_h1 = False
        self.title = ""
        self._h1 = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_HTML_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "h1":
            self._in_h1 = True
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_HTML_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        if self._in_h1:
            self._h1 += data
        self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)

    def best_title(self, fallback: str) -> str:
        return self.title.strip() or self._h1.strip() or fallback


def _title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:120]
    return fallback


def extract_document(raw: str, name: str, suffix: str) -> tuple[str, str]:
    """Deterministically produce (title, plain-text body) for a supported file."""
    if suffix in {".html", ".htm"}:
        parser = _TextExtractor()
        parser.feed(raw)
        return parser.best_title(name), parser.text()
    return _title_from_text(raw, name), raw


def _iter_inputs(path: Path) -> tuple[list[tuple[str, str]], list[str], list[FileFailure]]:
    """Yield (name, raw text) for every supported input; report the rest."""
    supported: list[tuple[str, str]] = []
    unsupported: list[str] = []
    failures: list[FileFailure] = []

    def read_file(file_path: Path) -> None:
        if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            unsupported.append(file_path.name)
            return
        try:
            supported.append((file_path.name, file_path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as exc:
            failures.append(FileFailure(name=file_path.name, reason=str(exc)))

    if path.is_dir():
        for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
            read_file(file_path)
    elif path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                for entry in sorted(archive.namelist()):
                    entry_name = Path(entry).name
                    if not entry_name or entry.endswith("/"):
                        continue
                    if Path(entry_name).suffix.lower() not in SUPPORTED_SUFFIXES:
                        unsupported.append(entry_name)
                        continue
                    try:
                        supported.append((entry_name, archive.read(entry).decode("utf-8")))
                    except UnicodeDecodeError as exc:
                        failures.append(FileFailure(name=entry_name, reason=str(exc)))
        except (OSError, zipfile.BadZipFile) as exc:
            raise IngestError(
                f"could not read archive {path} ({exc}). Nothing was added; "
                "check the file and re-run 'wingman corpus add'."
            ) from exc
    elif path.is_file():
        read_file(path)
    else:
        raise IngestError(f"{path} does not exist. Nothing was added.")
    return supported, unsupported, failures


def add_to_corpus(
    path: Path, source_type: str, config: Config, storage: Storage
) -> CorpusAddReport:
    supported, unsupported, failures = _iter_inputs(path)
    added = 0
    skipped = 0
    titles: list[str] = []
    for name, raw in supported:
        if not raw.strip():
            failures.append(FileFailure(name=name, reason="file is empty"))
            continue
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        record = storage.get_source_record_by_hash(content_hash)
        if record is not None and storage.find_corpus_document_by_source(record.record_id):
            skipped += 1
            continue
        if record is None:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            stored = config.inbox_dir / f"{stamp}-{content_hash[:8]}-{name}"
            stored.write_text(raw, encoding="utf-8")
            record = SourceRecord(
                source_type=source_type,
                source_locator=str(stored.relative_to(config.data_dir.resolve()))
                if stored.is_relative_to(config.data_dir.resolve())
                else str(stored),
                content_hash=content_hash,
            )
            storage.add_source_record(record)
        title, body = extract_document(raw, name, Path(name).suffix.lower())
        if not body.strip():
            failures.append(FileFailure(name=name, reason="no text could be extracted"))
            continue
        document = CorpusDocument(
            source_record_id=record.record_id,
            source_type=source_type,
            title=title,
            word_count=len(body.split()),
        )
        storage.add_corpus_document(document, body)
        added += 1
        titles.append(title)
    _logger.info(
        "corpus_add path=%s source_type=%s added=%d skipped=%d unsupported=%d failures=%d",
        path,
        source_type,
        added,
        skipped,
        len(unsupported),
        len(failures),
    )
    return CorpusAddReport(
        added=added,
        skipped_duplicates=skipped,
        skipped_unsupported=unsupported,
        failures=failures,
        titles=titles,
    )


def find_evidence(query: str, storage: Storage, limit: int = 10) -> list[EvidenceHit]:
    """Search the corpus and return cited excerpts."""
    hits: list[EvidenceHit] = []
    for document, snippet in storage.search_corpus(query, limit=limit):
        record = storage.get_source_record(document.source_record_id)
        hits.append(
            EvidenceHit(
                document=document,
                snippet=snippet,
                source_locator=record.source_locator if record else "unknown",
            )
        )
    return hits
