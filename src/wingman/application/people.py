"""People: the watchlist, seeded from explicit imports, fed by public feeds.

Entirely deterministic — no model calls. Three operations:

- add_person: manual watchlist entry, optionally with a Substack URL.
- seed_from_connections: create Person records from a LinkedIn export's
  Connections.csv. Deliberate PII minimization: names, profile URLs, company,
  position, and connection date are kept; email addresses are never read into
  a Person, and the CSV itself is not copied into the workspace — the
  SourceRecord's locator points at the export zip on the user's disk, and its
  content hash proves which bytes were consumed.
- fetch_person_feed: read a person's public sources — Substack, any RSS 2.0
  or Atom feed (Medium, WordPress, Ghost), or a configured blog index page
  on a feed-less site (RFC-009/RFC-011) — storing each post as an immutable
  SourceRecord plus an ExternalDocument indexed for full-text search.
- discover_feed / attach_feed: add-time feed discovery (direct URL, HTML
  autodiscovery, conventional paths), always confirm-gated by the caller.
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
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field

from wingman.application.corpus import extract_document
from wingman.application.ingest import IngestError
from wingman.domain import SourceRecord
from wingman.domain.person import (
    ExternalDocument,
    ExternalEvidenceHit,
    FeedAttribution,
    FeedKind,
    FeedSource,
    Person,
    PersonOrigin,
)
from wingman.infrastructure.config import Config
from wingman.infrastructure.fetch import FetchError, fetch_url
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.people")

CONNECTIONS_CSV = "Connections.csv"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


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
    """Extract (title, link, published, html) per entry from RSS 2.0 or Atom."""
    try:
        root = ET.fromstring(feed_bytes)
    except ET.ParseError as exc:
        raise IngestError(
            f"feed at {feed_url} is not parseable RSS/Atom ({exc}). Nothing was added."
        ) from exc
    items: list[dict[str, str]] = []
    if root.tag == f"{_ATOM_NS}feed":
        for entry in root.iter(f"{_ATOM_NS}entry"):
            link = ""
            for anchor in entry.iter(f"{_ATOM_NS}link"):
                rel = anchor.get("rel", "alternate")
                if rel == "alternate":
                    link = (anchor.get("href") or "").strip()
                    break
            items.append(
                {
                    "title": (entry.findtext(f"{_ATOM_NS}title") or "").strip(),
                    "link": link,
                    "published": (
                        entry.findtext(f"{_ATOM_NS}published")
                        or entry.findtext(f"{_ATOM_NS}updated")
                        or ""
                    ).strip(),
                    "html": (
                        entry.findtext(f"{_ATOM_NS}content")
                        or entry.findtext(f"{_ATOM_NS}summary")
                        or ""
                    ).strip(),
                }
            )
        return items
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
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "post"


class _Tally(BaseModel):
    items: int = 0
    added: int = 0
    skipped_duplicates: int = 0
    skipped_empty: int = 0
    titles: list[str] = Field(default_factory=list)


def _ingest_post(
    person: Person,
    source: FeedSource,
    title: str,
    link: str,
    published: str,
    raw_html: str,
    source_type: str,
    config: Config,
    storage: Storage,
    tally: _Tally,
) -> None:
    """Validate, archive, and index one post through the provenance pipeline."""
    # Extract and validate before persisting, so a skipped item leaves
    # no orphaned SourceRecord or inbox artifact behind.
    extracted_title, body = extract_document(raw_html, title or link or "untitled post", ".html")
    title = title or extracted_title
    if not body.strip():
        tally.skipped_empty += 1
        return
    content_hash = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()
    record = storage.get_source_record_by_hash(content_hash)
    if record is not None and storage.find_external_document_by_source(record.record_id):
        tally.skipped_duplicates += 1
        return
    if record is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        stored = config.inbox_dir / f"{stamp}-{content_hash[:8]}-{_slug(title)}.html"
        stored.write_text(raw_html, encoding="utf-8")
        record = SourceRecord(
            source_type=source_type,
            source_locator=str(stored.relative_to(config.data_dir.resolve()))
            if stored.is_relative_to(config.data_dir.resolve())
            else str(stored),
            content_hash=content_hash,
        )
        storage.add_source_record(record)
    organization = source.org_name if source.attribution == FeedAttribution.ORGANIZATION else None
    document = ExternalDocument(
        source_record_id=record.record_id,
        person_id=person.person_id,
        source_type=source_type,
        title=title,
        url=link or None,
        organization=organization,
        published_at=_published_at(published),
        word_count=len(body.split()),
    )
    storage.add_external_document(document, body)
    tally.added += 1
    tally.titles.append(title)


class _IndexLinkParser(HTMLParser):
    """Collect anchor hrefs from a blog index page, deterministically."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def _index_post_links(page_bytes: bytes, index_url: str) -> list[str]:
    """Links on the index page that live under the index path itself."""
    parser = _IndexLinkParser()
    parser.feed(page_bytes.decode("utf-8", errors="replace"))
    index = urlsplit(index_url.rstrip("/"))
    links: list[str] = []
    for href in parser.hrefs:
        absolute = urljoin(index_url.rstrip("/") + "/", href)
        parts = urlsplit(absolute)
        cleaned = parts._replace(query="", fragment="").geturl().rstrip("/")
        if parts.scheme != "https" or parts.netloc != index.netloc:
            continue
        if not cleaned.startswith(index.geturl() + "/") or cleaned == index.geturl():
            continue
        if cleaned not in links:
            links.append(cleaned)
    return links


INDEX_PAGE_LIMIT = 10  # newest-first pages fetched per run; bounded, never a crawl


def _fetch_index_source(
    person: Person,
    source: FeedSource,
    config: Config,
    storage: Storage,
    fetch: Callable[[str], bytes],
    tally: _Tally,
) -> None:
    page_bytes = fetch(source.url)
    fetched = 0
    for link in _index_post_links(page_bytes, source.url):
        if fetched >= INDEX_PAGE_LIMIT:
            break
        if storage.has_external_url(link):
            continue
        fetched += 1
        tally.items += 1
        raw_html = fetch(link).decode("utf-8", errors="replace")
        _ingest_post(person, source, "", link, "", raw_html, "web_page", config, storage, tally)


def fetch_person_feed(
    person: Person,
    config: Config,
    storage: Storage,
    fetcher: Callable[[str], bytes] | None = None,
) -> FeedFetchReport:
    """Fetch every configured source for a person and index new posts (RFC-009/011)."""
    sources = person.sources
    if not sources:
        raise IngestError(
            f"{person.name} has no sources. Nothing was fetched; add one with "
            f"'wingman people add \"{person.name}\" --substack <url>' or "
            f"'wingman people add-feed \"{person.name}\" <url>'."
        )
    fetch = fetcher if fetcher is not None else fetch_url
    tally = _Tally()
    for source in sources:
        try:
            if source.kind == FeedKind.INDEX_PAGE:
                _fetch_index_source(person, source, config, storage, fetch, tally)
                continue
            feed_bytes = fetch(source.url)
            items = _parse_feed_items(feed_bytes, source.url)
            tally.items += len(items)
            # The Substack-derived source is the one synthesized from
            # substack_url — reliable even for custom-domain publications.
            substack_feed = (
                person.substack_url.rstrip("/") + "/feed" if person.substack_url else None
            )
            source_type = "substack_feed" if source.url == substack_feed else "rss_feed"
            for item in items:
                _ingest_post(
                    person,
                    source,
                    item["title"],
                    item["link"],
                    item["published"],
                    item["html"],
                    source_type,
                    config,
                    storage,
                    tally,
                )
        except FetchError as exc:
            raise IngestError(f"{exc}. Nothing further was added for this source.") from exc
    _logger.info(
        "feed_fetch person=%s sources=%d items=%d added=%d skipped_dup=%d skipped_empty=%d",
        person.name,
        len(sources),
        tally.items,
        tally.added,
        tally.skipped_duplicates,
        tally.skipped_empty,
    )
    return FeedFetchReport(
        person_name=person.name,
        feed_url=", ".join(source.url for source in sources),
        items=tally.items,
        added=tally.added,
        skipped_duplicates=tally.skipped_duplicates,
        skipped_empty=tally.skipped_empty,
        titles=tally.titles,
    )


class FeedDiscovery(BaseModel):
    """What one add-time discovery pass found (RFC-011): a feed, or nothing."""

    feed_url: str | None = None
    feed_title: str | None = None
    probed: list[str] = Field(default_factory=list)


# Bounded conventional probe list — an enumerable set of GETs, never a crawl.
CONVENTIONAL_FEED_PATHS = ["/feed", "/rss", "/rss.xml", "/atom.xml", "/index.xml", "/feed.xml"]


def _feed_title(data: bytes) -> str | None:
    """The feed's own title if data parses as RSS 2.0 or Atom, else None."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    if root.tag == "rss":
        return (root.findtext("channel/title") or "").strip() or "untitled feed"
    if root.tag == f"{_ATOM_NS}feed":
        return (root.findtext(f"{_ATOM_NS}title") or "").strip() or "untitled feed"
    return None


class _AlternateLinkParser(HTMLParser):
    """Find <link rel="alternate" type="application/rss+xml|atom+xml"> in page HTML."""

    _FEED_TYPES = {"application/rss+xml", "application/atom+xml"}

    def __init__(self) -> None:
        super().__init__()
        self.feed_hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        by_name = {name: (value or "") for name, value in attrs}
        if "alternate" in by_name.get("rel", "").lower().split() and (
            by_name.get("type", "").lower() in self._FEED_TYPES
        ):
            href = by_name.get("href", "").strip()
            if href:
                self.feed_hrefs.append(href)


def discover_feed(url: str, fetcher: Callable[[str], bytes] | None = None) -> FeedDiscovery:
    """Find a feed for a URL: direct feed, HTML autodiscovery, then conventional paths.

    A bounded add-time operation: one GET of the given URL, plus at most one
    GET per conventional path if autodiscovery finds nothing. Never attaches
    anything — the caller confirms with the user first (the same name can
    belong to different people; discovery can succeed on the wrong human).
    """
    if not url.startswith("https://"):
        raise IngestError(f"only https:// URLs are supported (RFC-009); got {url!r}")
    fetch = fetcher if fetcher is not None else fetch_url
    probed = [url]
    try:
        data = fetch(url)
    except FetchError as exc:
        raise IngestError(f"{exc}. Nothing was attached.") from exc
    title = _feed_title(data)
    if title is not None:
        return FeedDiscovery(feed_url=url, feed_title=title, probed=probed)
    parser = _AlternateLinkParser()
    parser.feed(data.decode("utf-8", errors="replace"))
    for href in parser.feed_hrefs:
        candidate = urljoin(url, href)
        if not candidate.startswith("https://"):
            continue
        probed.append(candidate)
        try:
            candidate_title = _feed_title(fetch(candidate))
        except FetchError:
            continue
        if candidate_title is not None:
            return FeedDiscovery(feed_url=candidate, feed_title=candidate_title, probed=probed)
    base = url.rstrip("/")
    for path in CONVENTIONAL_FEED_PATHS:
        candidate = base + path
        probed.append(candidate)
        try:
            candidate_title = _feed_title(fetch(candidate))
        except FetchError:
            continue
        if candidate_title is not None:
            return FeedDiscovery(feed_url=candidate, feed_title=candidate_title, probed=probed)
    return FeedDiscovery(probed=probed)


def attach_feed(person: Person, source: FeedSource, storage: Storage) -> Person:
    """Attach a confirmed feed source to a person, enforcing the RFC-009/011 invariants.

    URLs are normalized (trailing slash stripped) and must be HTTPS even when
    this is called programmatically; organization attribution requires an
    org_name; duplicates (up to normalization) are rejected.
    """
    normalized = source.url.rstrip("/")
    if not normalized.startswith("https://"):
        raise IngestError(f"only https:// sources are supported (RFC-009); got {source.url!r}")
    if source.attribution == FeedAttribution.ORGANIZATION and not (source.org_name or "").strip():
        raise IngestError("organization-attributed sources need an organization name; pass --org.")
    source = source.model_copy(update={"url": normalized})
    existing_urls = {feed.url.rstrip("/") for feed in person.sources}
    if normalized in existing_urls:
        raise IngestError(f"{person.name} already has the source {normalized}.")
    updated = person.model_copy(update={"feeds": [*person.feeds, source]})
    storage.update_person(updated)
    return updated


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
