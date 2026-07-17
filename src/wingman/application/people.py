"""People: the watchlist, seeded from explicit imports, fed by public feeds.

Entirely deterministic — no model calls. Three operations:

- add_person: manual watchlist entry, optionally with a Substack URL.
- seed_from_connections: create Person records from a LinkedIn export's
  Connections.csv. Deliberate PII minimization: names, profile URLs, company,
  position, and connection date are kept; email addresses are never read into
  a Person, and the CSV itself is not copied into the workspace — the
  SourceRecord's locator points at the export zip on the user's disk, and its
  content hash proves which bytes were consumed.
- fetch_person_feed: read a person's public Substack RSS feed (the one
  network read in Wingman, RFC-009) and store each post as an immutable
  SourceRecord plus an ExternalDocument indexed for full-text search.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from pydantic import BaseModel, Field

from wingman.application.corpus import extract_document
from wingman.application.ingest import IngestError
from wingman.domain import SourceRecord
from wingman.domain.person import ExternalDocument, ExternalEvidenceHit, Person, PersonOrigin
from wingman.infrastructure.config import Config
from wingman.infrastructure.fetch import FetchError, fetch_url
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.people")

CONNECTIONS_CSV = "Connections.csv"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"


class ConnectionsSeedReport(BaseModel):
    created: int
    skipped_existing: int
    skipped_incomplete: int


class FeedFetchReport(BaseModel):
    person_name: str
    feed_url: str
    items: int
    added: int
    skipped_duplicates: int
    skipped_empty: int
    titles: list[str] = Field(default_factory=list)


def add_person(
    name: str,
    storage: Storage,
    substack_url: str | None = None,
    company: str | None = None,
    position: str | None = None,
) -> tuple[Person, bool]:
    """Add a person to the watchlist; updates the existing record if the name is known.

    Returns (person, created) — created is False when an existing person was updated.
    """
    if substack_url is not None:
        substack_url = substack_url.rstrip("/")
        if not substack_url.startswith("https://"):
            raise IngestError(
                f"Substack URL must start with https:// (RFC-009); got {substack_url!r}"
            )
    candidate = Person(
        name=name,
        origin=PersonOrigin.MANUAL,
        substack_url=substack_url,
        company=company,
        position=position,
    )
    existing = storage.find_person_by_name_key(candidate.name_key)
    if existing is None:
        storage.add_person(candidate)
        return candidate, True
    updated = existing.model_copy(
        update={
            "substack_url": substack_url or existing.substack_url,
            "company": company or existing.company,
            "position": position or existing.position,
        }
    )
    storage.update_person(updated)
    return updated, False


def _connections_rows(raw: str) -> list[dict[str, str]]:
    """Parse Connections.csv, skipping LinkedIn's free-text 'Notes:' preamble."""
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("First Name"):
            return list(csv.DictReader(io.StringIO("\n".join(lines[index:]))))
    raise IngestError(
        "Connections.csv has no 'First Name' header row. Nothing was seeded; "
        "check that this is a LinkedIn data export."
    )


def seed_from_connections(export_path: Path, storage: Storage) -> ConnectionsSeedReport:
    """Create Person records from a LinkedIn export's Connections.csv."""
    try:
        with zipfile.ZipFile(export_path) as archive:
            entry_name = next(
                (name for name in sorted(archive.namelist()) if Path(name).name == CONNECTIONS_CSV),
                None,
            )
            if entry_name is None:
                raise IngestError(
                    f"no {CONNECTIONS_CSV} found in {export_path}. Nothing was seeded."
                )
            raw_bytes = archive.read(entry_name)
    except (OSError, zipfile.BadZipFile) as exc:
        raise IngestError(
            f"could not read {export_path} ({exc}). Nothing was seeded; "
            "check the path and re-run 'wingman people import-connections'."
        ) from exc
    try:
        raw = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestError(
            f"{CONNECTIONS_CSV} in the export could not be decoded ({exc}). Nothing was seeded."
        ) from exc

    rows = _connections_rows(raw)
    # Hash the exact bytes read from the archive (not the decoded text), so the
    # hash identifies the exported artifact itself.
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    record = storage.get_source_record_by_hash(content_hash)
    if record is None:
        # The CSV holds contact emails, so the bytes stay in the user's export
        # zip — only the locator and hash are recorded (PII minimization).
        record = SourceRecord(
            source_type="linkedin_connections",
            source_locator=f"{export_path}!{entry_name}",
            content_hash=content_hash,
        )
        storage.add_source_record(record)

    created = 0
    skipped_existing = 0
    skipped_incomplete = 0
    for row in rows:
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        name = " ".join(part for part in (first, last) if part)
        if not name:
            skipped_incomplete += 1
            continue
        person = Person(
            name=name,
            origin=PersonOrigin.LINKEDIN_CONNECTIONS,
            linkedin_url=(row.get("URL") or "").strip() or None,
            company=(row.get("Company") or "").strip() or None,
            position=(row.get("Position") or "").strip() or None,
            connected_on=(row.get("Connected On") or "").strip() or None,
            source_record_id=record.record_id,
        )
        if storage.find_person_by_name_key(person.name_key) is not None:
            skipped_existing += 1
            continue
        storage.add_person(person)
        created += 1
    _logger.info(
        "connections_seed export=%s created=%d skipped_existing=%d skipped_incomplete=%d",
        export_path,
        created,
        skipped_existing,
        skipped_incomplete,
    )
    return ConnectionsSeedReport(
        created=created,
        skipped_existing=skipped_existing,
        skipped_incomplete=skipped_incomplete,
    )


def _parse_feed_items(feed_bytes: bytes, feed_url: str) -> list[dict[str, str]]:
    """Extract (title, link, published, html) for each RSS item, deterministically."""
    try:
        root = ET.fromstring(feed_bytes)
    except ET.ParseError as exc:
        raise IngestError(
            f"feed at {feed_url} is not parseable RSS ({exc}). Nothing was added."
        ) from exc
    items: list[dict[str, str]] = []
    for item in root.iter("item"):
        items.append(
            {
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "published": (item.findtext("pubDate") or "").strip(),
                "html": (
                    item.findtext(f"{_CONTENT_NS}encoded") or item.findtext("description") or ""
                ).strip(),
            }
        )
    return items


def _published_at(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "post"


def fetch_person_feed(
    person: Person,
    config: Config,
    storage: Storage,
    fetcher: Callable[[str], bytes] | None = None,
) -> FeedFetchReport:
    """Fetch a person's public Substack feed and index new posts."""
    if not person.substack_url:
        raise IngestError(
            f"{person.name} has no Substack URL. Nothing was fetched; set one with "
            f"'wingman people add \"{person.name}\" --substack <url>'."
        )
    feed_url = person.substack_url.rstrip("/") + "/feed"
    fetch = fetcher if fetcher is not None else fetch_url
    try:
        feed_bytes = fetch(feed_url)
    except FetchError as exc:
        raise IngestError(f"{exc}. Nothing was added.") from exc

    items = _parse_feed_items(feed_bytes, feed_url)
    added = 0
    skipped_duplicates = 0
    skipped_empty = 0
    titles: list[str] = []
    for item in items:
        title = item["title"] or item["link"] or "untitled post"
        # Extract and validate before persisting, so a skipped item leaves
        # no orphaned SourceRecord or inbox artifact behind.
        _, body = extract_document(item["html"], title, ".html")
        if not body.strip():
            skipped_empty += 1
            continue
        raw = item["html"]
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        record = storage.get_source_record_by_hash(content_hash)
        if record is not None and storage.find_external_document_by_source(record.record_id):
            skipped_duplicates += 1
            continue
        if record is None:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            stored = config.inbox_dir / f"{stamp}-{content_hash[:8]}-{_slug(title)}.html"
            stored.write_text(raw, encoding="utf-8")
            record = SourceRecord(
                source_type="substack_feed",
                source_locator=str(stored.relative_to(config.data_dir.resolve()))
                if stored.is_relative_to(config.data_dir.resolve())
                else str(stored),
                content_hash=content_hash,
            )
            storage.add_source_record(record)
        document = ExternalDocument(
            source_record_id=record.record_id,
            person_id=person.person_id,
            source_type="substack_feed",
            title=title,
            url=item["link"] or None,
            published_at=_published_at(item["published"]),
            word_count=len(body.split()),
        )
        storage.add_external_document(document, body)
        added += 1
        titles.append(title)
    _logger.info(
        "feed_fetch person=%s feed=%s items=%d added=%d skipped_dup=%d skipped_empty=%d",
        person.name,
        feed_url,
        len(items),
        added,
        skipped_duplicates,
        skipped_empty,
    )
    return FeedFetchReport(
        person_name=person.name,
        feed_url=feed_url,
        items=len(items),
        added=added,
        skipped_duplicates=skipped_duplicates,
        skipped_empty=skipped_empty,
        titles=titles,
    )


def find_people_evidence(
    query: str, storage: Storage, limit: int = 10
) -> list[ExternalEvidenceHit]:
    """Search people's writing and return excerpts attributed to their authors."""
    hits: list[ExternalEvidenceHit] = []
    for document, snippet in storage.search_external(query, limit=limit):
        person = storage.get_person(document.person_id)
        hits.append(
            ExternalEvidenceHit(
                document=document,
                snippet=snippet,
                person_name=person.name if person else "unknown",
            )
        )
    return hits
