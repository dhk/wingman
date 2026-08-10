"""Corpus ingestion and evidence search: the user's writing as citable sources.

Entirely deterministic — no model calls. Files (Markdown, plain text, HTML,
or a Substack-style export zip of HTML posts) become immutable SourceRecords
plus CorpusDocuments indexed for full-text search (RFC-007).
"""

from __future__ import annotations

import csv
import hashlib
import io
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


class _Candidate(BaseModel):
    """One supported input file, with optional metadata from an export manifest."""

    name: str
    raw: str
    title_override: str | None = None
    published_at: datetime | None = None


def _parse_posts_manifest(
    archive: zipfile.ZipFile,
) -> dict[str, tuple[str | None, datetime | None]] | None:
    """Read a root-level Substack-style posts.csv: post_id -> (title, publish date).

    Returns None when no root posts.csv exists or it cannot be parsed — the
    archive is then ingested without metadata rather than failing outright.
    """
    if "posts.csv" not in archive.namelist():
        return None
    manifest: dict[str, tuple[str | None, datetime | None]] = {}
    try:
        with archive.open("posts.csv") as handle:
            reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8"))
            for row in reader:
                post_id = (row.get("post_id") or "").strip()
                if not post_id:
                    continue
                title = (row.get("title") or "").strip() or None
                published: datetime | None = None
                raw_date = (row.get("post_date") or "").strip()
                if raw_date:
                    try:
                        published = datetime.fromisoformat(raw_date)
                    except ValueError:
                        published = None
                manifest[post_id] = (title, published)
    except (UnicodeDecodeError, csv.Error) as exc:
        _logger.warning("posts.csv manifest could not be parsed (%s); ingesting without it", exc)
        return None
    return manifest


def _iter_inputs(path: Path) -> tuple[list[_Candidate], list[str], list[FileFailure]]:
    """Collect every supported input file; report the rest."""
    supported: list[_Candidate] = []
    unsupported: list[str] = []
    failures: list[FileFailure] = []

    def read_file(file_path: Path) -> None:
        if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            unsupported.append(file_path.name)
            return
        try:
            supported.append(
                _Candidate(name=file_path.name, raw=file_path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeDecodeError) as exc:
            failures.append(FileFailure(name=file_path.name, reason=str(exc)))

    if path.is_dir():
        for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
            read_file(file_path)
    elif path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = _parse_posts_manifest(archive)
                for entry in sorted(archive.namelist()):
                    entry_name = Path(entry).name
                    if not entry_name or entry.endswith("/"):
                        continue
                    if entry == "posts.csv" and manifest is not None:
                        continue  # the root manifest is consumed as metadata, not content
                    if Path(entry_name).suffix.lower() not in SUPPORTED_SUFFIXES:
                        unsupported.append(entry_name)
                        continue
                    try:
                        raw = archive.read(entry).decode("utf-8")
                    except UnicodeDecodeError as exc:
                        failures.append(FileFailure(name=entry_name, reason=str(exc)))
                        continue
                    title, published = (manifest or {}).get(Path(entry_name).stem, (None, None))
                    supported.append(
                        _Candidate(
                            name=entry_name,
                            raw=raw,
                            title_override=title,
                            published_at=published,
                        )
                    )
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
    for candidate in supported:
        name, raw = candidate.name, candidate.raw
        if not raw.strip():
            failures.append(FileFailure(name=name, reason="file is empty"))
            continue
        # Extract and validate before persisting anything, so a failed add
        # leaves no orphaned SourceRecord or inbox artifact behind.
        title, body = extract_document(raw, name, Path(name).suffix.lower())
        if candidate.title_override:
            title = candidate.title_override
        if not body.strip():
            failures.append(FileFailure(name=name, reason="no text could be extracted"))
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
        document = CorpusDocument(
            source_record_id=record.record_id,
            source_type=source_type,
            title=title,
            published_at=candidate.published_at,
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
