"""People: the watchlist, seeded from explicit imports, fed by public feeds.

Entirely deterministic — no model calls. Three operations:

- add_person: manual watchlist entry, optionally with a public writing URL.
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
from typing import ClassVar
from urllib.error import HTTPError
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
from wingman.infrastructure.storage import DuplicateRecordError, Storage

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
    failed_sources: list[str] = Field(default_factory=list)
    corrected_sources: list[str] = Field(default_factory=list)


def _resolve_writing_feed(url: str, fetcher: Callable[[str], bytes]) -> str:
    """Resolve an entered writing URL using bounded RSS/Atom discovery."""
    try:
        discovery = discover_feed(url, fetcher=fetcher)
    except IngestError as exc:
        raise IngestError(
            f"writing URL {url!r} could not be fetched. Use add-feed or feed_discover "
            f"to check the configured URL. {exc}"
        ) from exc
    if discovery.feed_url is None:
        raise IngestError(
            f"no feed was discoverable at writing URL {url!r}; "
            "use add-feed with --index (MCP: feed_attach(kind='index_page')) "
            "for a blog index, or supply a verified RSS/Atom URL."
        )
    return discovery.feed_url


def add_person(
    name: str,
    storage: Storage,
    substack_url: str | None = None,
    company: str | None = None,
    position: str | None = None,
    linkedin_url: str | None = None,
    email: str | None = None,
    fetcher: Callable[[str], bytes] | None = None,
    strict: bool = True,
) -> tuple[Person, bool]:
    """Add a person to the watchlist; updates the existing record if the name is known.

    Returns (person, created) — created is False when an existing person was
    updated. email is a manual-entry field only: imports never read email
    addresses. A passed writing URL uses bounded feed discovery and is verified
    real and parseable before it's stored — nothing is written if it isn't
    (#483); fetcher exists only as the test seam, matching every other feed
    function in this module (fetch_person_feed, discover_feed, ...). strict
    is the one narrow escape hatch: False stores the URL even if it doesn't
    verify, for a caller (demo seeding) whose own next step already
    discovers and reports an unreachable feed gracefully rather than
    rejecting the person outright — never for a live person typing a URL.
    """
    writing_feed_url = None
    if substack_url is not None:
        substack_url = substack_url.rstrip("/")
        if not substack_url.startswith("https://"):
            raise IngestError(
                f"Writing URL must start with https:// (RFC-009); got {substack_url!r}"
            )
        try:
            writing_feed_url = _resolve_writing_feed(
                substack_url, fetcher if fetcher is not None else fetch_url
            )
        except IngestError:
            if strict:
                raise
    if linkedin_url is not None:
        linkedin_url = linkedin_url.rstrip("/")
        if not linkedin_url.startswith("https://"):
            raise IngestError(
                f"LinkedIn URL must start with https:// (RFC-009); got {linkedin_url!r}"
            )
    if email is not None and ("@" not in email or " " in email.strip()):
        raise IngestError(f"{email!r} does not look like an email address.")
    candidate = Person(
        name=name,
        origin=PersonOrigin.MANUAL,
        substack_url=substack_url,
        writing_feed_url=writing_feed_url,
        company=company,
        position=position,
        linkedin_url=linkedin_url,
        email=email.strip() if email else None,
    )
    existing = storage.find_person_by_name_key(candidate.name_key)
    if existing is None:
        storage.add_person(candidate)
        return candidate, True
    updated = existing.model_copy(
        update={
            "substack_url": substack_url or existing.substack_url,
            "writing_feed_kind": FeedKind.RSS
            if substack_url is not None
            else existing.writing_feed_kind,
            "writing_feed_url": writing_feed_url
            if substack_url is not None
            else existing.writing_feed_url,
            "company": company or existing.company,
            "position": position or existing.position,
            "linkedin_url": linkedin_url or existing.linkedin_url,
            "email": (email.strip() if email else None) or existing.email,
        }
    )
    storage.update_person(updated)
    return updated, False


def match_people(storage: Storage, query: str) -> list[Person]:
    """People whose names match a possibly-partial query, exact key first.

    An exact normalized-name match wins outright. Otherwise a person matches
    when the query is a substring of their normalized name or every query
    token appears among their name's tokens ('Marko' finds 'Marko Klopets';
    'klopets marko' does too). Deterministic and ordered by name.
    """
    query_key = " ".join(query.lower().split())
    if not query_key:
        return []
    exact = storage.find_person_by_name_key(query_key)
    if exact is not None:
        return [exact]
    tokens = set(query_key.split())
    matches = [
        person
        for person in storage.list_people()
        if query_key in person.name_key or tokens <= set(person.name_key.split())
    ]
    return sorted(matches, key=lambda person: person.name)


def _require_single_match(storage: Storage, query: str) -> Person:
    matches = match_people(storage, query)
    if not matches:
        raise IngestError(f"no person matching {query!r}.")
    if len(matches) > 1:
        names = ", ".join(person.name for person in matches[:5])
        raise IngestError(f"{query!r} matches multiple people: {names}. Use the full name.")
    return matches[0]


def _refuse_company_anchor(person: Person, action: str) -> None:
    # Company anchors (RFC-029) are system-owned carriers of company feeds:
    # people-surface mutations would orphan or shadow them.
    if person.person_id.startswith("__company__"):
        raise IngestError(
            f"{person.name!r} is a company feed anchor, not a person; {action} the company "
            "instead ('wingman company …', company_manage/company_feed via MCP)."
        )


def rename_person(current: str, new_name: str, storage: Storage) -> Person:
    """Rename a watchlist person in place; person_id and all their data are untouched."""
    person = _require_single_match(storage, current)
    _refuse_company_anchor(person, "rename")
    new_name = new_name.strip()
    if not new_name:
        raise IngestError("new name is empty.")
    try:
        return storage.rename_person(person.person_id, new_name)
    except DuplicateRecordError as exc:
        raise IngestError(
            f"{exc} — use 'fix' instead of 'rename' if you want to merge into them."
        ) from exc


def delete_person(current: str, storage: Storage) -> Person:
    """Delete a watchlist person and everything keyed to them."""
    person = _require_single_match(storage, current)
    _refuse_company_anchor(person, "delete")
    storage.delete_person(person.person_id)
    return person


def fix_person(current: str, correct_name: str, storage: Storage) -> tuple[Person, bool]:
    """Correct a person's name. If `correct_name` already belongs to someone else,
    `current` is merged into them (their blank fields filled, documents/POV card/outreach
    brief moved over) instead of creating a name collision. Returns (final_person, merged)."""
    person = _require_single_match(storage, current)
    _refuse_company_anchor(person, "fix")
    correct_name = correct_name.strip()
    if not correct_name:
        raise IngestError("correct name is empty.")
    target_key = " ".join(correct_name.lower().split())
    target = storage.find_person_by_name_key(target_key)
    if target is not None and target.person_id != person.person_id:
        return storage.merge_person(keep_id=target.person_id, absorb_id=person.person_id), True
    return storage.rename_person(person.person_id, correct_name), False


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
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    # A date string with no timezone token (RFC-822 or ISO) parses naive;
    # every caller compares this against an aware datetime (e.g. the news
    # staleness cutoff), which raises TypeError on a naive/aware mismatch.
    # Assume UTC rather than guess a local zone — the feeds this reads are
    # publicly syndicated, not tied to any particular reader's timezone.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


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


def _recover_feed_source(
    source: FeedSource, fetch: Callable[[str], bytes], *, missing: bool
) -> FeedSource:
    """Correct source format using the existing bounded discovery policy."""
    parts = urlsplit(source.url)
    path = parts.path.rstrip("/")
    if missing and "/" + path.rsplit("/", 1)[-1] in CONVENTIONAL_FEED_PATHS:
        path = path.rsplit("/", 1)[0]
    index_url = parts._replace(path=path, query="", fragment="").geturl().rstrip("/")
    cache: dict[str, bytes] = {}

    def cached(url: str) -> bytes:
        if url not in cache:
            cache[url] = fetch(url)
        return cache[url]

    try:
        discovered = discover_feed(index_url, fetcher=cached)
    except IngestError as exc:
        raise FetchError(
            f"no feed was discoverable at {source.url!r}; index recovery failed: {exc}"
        ) from exc
    if discovered.feed_url:
        return source.model_copy(update={"url": discovered.feed_url})
    data = cache[index_url].lower()
    if not any(tag in data for tag in (b"<html", b"<body", b"<a ", b"<div")):
        raise FetchError(
            f"no feed was discoverable at {source.url!r}; no readable HTML index found"
        )
    return source.model_copy(update={"url": index_url, "kind": FeedKind.INDEX_PAGE})


def _save_source_correction(
    person: Person, old: FeedSource, corrected: FeedSource, storage: Storage
) -> None:
    current = storage.get_person(person.person_id)
    if current is None:
        return
    if old.url in {person.substack_url, person.writing_feed_url}:
        updated = current.model_copy(
            update={"writing_feed_url": corrected.url, "writing_feed_kind": corrected.kind}
        )
    else:
        updated = current.model_copy(
            update={"feeds": [corrected if item == old else item for item in current.feeds]}
        )
    storage.update_person(updated)


def fetch_person_feed(
    person: Person,
    config: Config,
    storage: Storage,
    fetcher: Callable[[str], bytes] | None = None,
) -> FeedFetchReport:
    """Fetch every configured source for a person and index new posts (RFC-009/011).

    One dead source is isolated and reported, not fatal to the rest — a
    person with several feeds still gets writing indexed from the live ones
    even when one has gone stale (#overnight-P2). Only when every source
    fails does the whole call raise.
    """
    sources = person.sources
    if not sources:
        raise IngestError(
            f"{person.name} has no sources. Nothing was fetched; add one with "
            f"'wingman people add \"{person.name}\" --substack <url>' or "
            f"'wingman people add-feed \"{person.name}\" <url>'."
        )
    fetch = fetcher if fetcher is not None else fetch_url
    tally = _Tally()
    failed_sources: list[str] = []
    corrected_sources: list[str] = []
    for source in sources:
        entered_url = (
            person.substack_url
            if source.url in {person.substack_url, person.writing_feed_url}
            else source.url
        )
        try:
            if source.kind == FeedKind.INDEX_PAGE:
                _fetch_index_source(person, source, config, storage, fetch, tally)
                continue
            original = source
            try:
                feed_bytes = fetch(source.url)
            except FetchError as exc:
                if not isinstance(exc.__cause__, HTTPError) or exc.__cause__.code != 404:
                    raise
                source = _recover_feed_source(source, fetch, missing=True)
                feed_bytes = b""
            if source == original and _feed_title(feed_bytes) is None:
                source = _recover_feed_source(source, fetch, missing=False)
                feed_bytes = b""
            if source.kind == FeedKind.INDEX_PAGE:
                _fetch_index_source(person, source, config, storage, fetch, tally)
                _save_source_correction(person, original, source, storage)
                corrected_sources.append(f"{original.url} → {source.url} ({source.kind.value})")
                continue
            if not feed_bytes:
                feed_bytes = fetch(source.url)
            if source != original:
                _save_source_correction(person, original, source, storage)
                corrected_sources.append(f"{original.url} → {source.url} ({source.kind.value})")
            items = _parse_feed_items(feed_bytes, source.url)
            tally.items += len(items)
            substack_feed = person.writing_feed_url or person.substack_url
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
            failed_sources.append(f"{entered_url}: {exc}")
    if len(failed_sources) == len(sources):
        raise IngestError(
            f"every source failed for {person.name}: "
            + "; ".join(failed_sources)
            + ". Nothing was added."
        )
    _logger.info(
        "feed_fetch person=%s sources=%d items=%d added=%d skipped_dup=%d skipped_empty=%d"
        " failed=%d",
        person.name,
        len(sources),
        tally.items,
        tally.added,
        tally.skipped_duplicates,
        tally.skipped_empty,
        len(failed_sources),
    )
    return FeedFetchReport(
        person_name=person.name,
        feed_url=", ".join(source.url for source in sources),
        items=tally.items,
        added=tally.added,
        skipped_duplicates=tally.skipped_duplicates,
        skipped_empty=tally.skipped_empty,
        titles=tally.titles,
        failed_sources=failed_sources,
        corrected_sources=corrected_sources,
    )


class FeedDiscovery(BaseModel):
    """What one add-time discovery pass found (RFC-011): a feed, or nothing."""

    feed_url: str | None = None
    feed_title: str | None = None
    probed: list[str] = Field(default_factory=list)


# Bounded conventional probe list — an enumerable set of GETs, never a crawl.
CONVENTIONAL_FEED_PATHS = ["/feed", "/rss", "/rss.xml", "/atom.xml", "/index.xml", "/feed.xml"]
# Caps on add-time discovery: at most this many feed-shaped page anchors are
# considered, and at most this many candidate GETs total.
_ANCHOR_CANDIDATE_LIMIT = 3
_MAX_FEED_PROBES = 16


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
    """Find feed candidates in page HTML: <link rel="alternate"> plus feed-ish anchors.

    Proper autodiscovery tags come first. Many site builders (Webflow
    prominently) configure an RSS feed but never emit the <link> tag — the
    only trace is an <a> to something like /blog/rss.xml, so anchors whose
    href looks like a feed are kept as second-tier candidates.
    """

    _FEED_TYPES: ClassVar[set[str]] = {"application/rss+xml", "application/atom+xml"}
    _ANCHOR_HINTS: ClassVar[tuple[str, ...]] = ("rss", "atom", "feed")

    def __init__(self) -> None:
        super().__init__()
        self.feed_hrefs: list[str] = []
        self.anchor_hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        by_name = {name: (value or "") for name, value in attrs}
        if tag == "link":
            if "alternate" in by_name.get("rel", "").lower().split() and (
                by_name.get("type", "").lower() in self._FEED_TYPES
            ):
                href = by_name.get("href", "").strip()
                if href:
                    self.feed_hrefs.append(href)
        elif tag == "a":
            href = by_name.get("href", "").strip()
            lowered = href.lower()
            if href and any(hint in lowered for hint in self._ANCHOR_HINTS):
                last = lowered.rstrip("/").rsplit("/", 1)[-1]
                # only hrefs whose final segment is feed-shaped ("rss.xml",
                # "feed", "atom.xml") — not every URL that mentions "feed"
                if last.split(".")[0] in self._ANCHOR_HINTS:
                    self.anchor_hrefs.append(href)


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

    # Candidate order: real autodiscovery tags, then feed-shaped anchors on
    # the page, then conventional paths under the given URL, then the same
    # paths at the site root (a company blog handed over as /blog often keeps
    # its feed at the root). Bounded and deduplicated — never a crawl.
    candidates: list[str] = []
    seen: set[str] = set(probed)

    def consider(candidate: str) -> None:
        if candidate.startswith("https://") and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    for href in parser.feed_hrefs:
        consider(urljoin(url, href))
    for href in parser.anchor_hrefs[:_ANCHOR_CANDIDATE_LIMIT]:
        consider(urljoin(url, href))
    base = url.rstrip("/")
    for path in CONVENTIONAL_FEED_PATHS:
        consider(base + path)
    parts = urlsplit(url)
    origin = f"https://{parts.netloc}"
    if origin != base:
        for path in CONVENTIONAL_FEED_PATHS:
            consider(origin + path)

    for candidate in candidates[:_MAX_FEED_PROBES]:
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


class RecommendationCandidate(BaseModel):
    """One publication your watched publications recommend but you don't watch."""

    url: str
    recommenders: list[str] = Field(default_factory=list)


class DiscoverReport(BaseModel):
    scanned: int
    failures: list[str] = Field(default_factory=list)
    candidates: list[RecommendationCandidate] = Field(default_factory=list)


# Substack system subdomains that appear in page chrome, never as publications.
_SUBSTACK_SYSTEM_SUBDOMAINS = {"www", "substack", "open", "support", "api", "cdn", "on", "reader"}


def _substack_publications_in_page(page_bytes: bytes, own_hostname: str) -> list[str]:
    """Publication roots (https://<pub>.substack.com) linked from a page.

    Uses parsed hostnames (never raw netlocs), so userinfo or ports in a
    crafted link can neither leak into candidate URLs nor dodge filtering.
    """
    parser = _IndexLinkParser()
    parser.feed(page_bytes.decode("utf-8", errors="replace"))
    found: list[str] = []
    for href in parser.hrefs:
        hostname = (urlsplit(href).hostname or "").lower()
        if not hostname.endswith(".substack.com") or hostname == own_hostname:
            continue
        subdomain = hostname.removesuffix(".substack.com")
        if not subdomain or subdomain in _SUBSTACK_SYSTEM_SUBDOMAINS:
            continue
        root = f"https://{hostname}"
        if root not in found:
            found.append(root)
    return found


def discover_recommendations(
    storage: Storage,
    fetcher: Callable[[str], bytes] | None = None,
    limit: int = 10,
) -> DiscoverReport:
    """Walk the public recommendations pages of watched Substacks (RFC-009).

    Suggestion-only: candidates are ranked by how many watched publications
    recommend them and returned for the user to add by hand — nothing is
    ever attached automatically. One page per watched publication, no
    deeper crawling. The page shape is unofficial; a page that yields no
    candidates simply contributes nothing.
    """
    fetch = fetcher if fetcher is not None else fetch_url
    watched = [person for person in storage.list_people() if person.substack_url]
    if not watched:
        return DiscoverReport(scanned=0)
    watched_hostnames = {
        hostname.lower()
        for person in storage.list_people()
        for source in person.sources
        if (hostname := urlsplit(source.url).hostname)
    } | {
        hostname.lower()
        for p in watched
        if p.substack_url and (hostname := urlsplit(p.substack_url).hostname)
    }
    recommenders: dict[str, list[str]] = {}
    failures: list[str] = []
    scanned = 0
    for person in watched:
        assert person.substack_url is not None
        base = person.substack_url.rstrip("/")
        own_hostname = (urlsplit(base).hostname or "").lower()
        try:
            page = fetch(base + "/recommendations")
        except FetchError as exc:
            failures.append(f"{person.name}: {exc}")
            continue
        scanned += 1
        for candidate in _substack_publications_in_page(page, own_hostname):
            if (urlsplit(candidate).hostname or "").lower() in watched_hostnames:
                continue
            recommenders.setdefault(candidate, [])
            if person.name not in recommenders[candidate]:
                recommenders[candidate].append(person.name)
    candidates = [
        RecommendationCandidate(url=url, recommenders=names) for url, names in recommenders.items()
    ]
    candidates.sort(key=lambda entry: (-len(entry.recommenders), entry.url))
    _logger.info(
        "discover scanned=%d failures=%d candidates=%d",
        scanned,
        len(failures),
        len(candidates),
    )
    return DiscoverReport(scanned=scanned, failures=failures, candidates=candidates[:limit])


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
