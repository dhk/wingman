"""Company research over user-approved sources (RFC-015, amended by RFC-060).

The network-scope decision RFC-012 deferred, resolved: the user names the
exact pages they trust for a company (a careers page, a newsroom), and
`wingman company research` makes one explicit, read-only HTTPS GET per
approved source (RFC-009 shape). What comes back is reduced to a
deterministic snapshot — a hash of the page's visible text and its set of
links — and the finding is the diff against the previous snapshot: new
links are the hiring/announcement signal, a changed hash the weaker "page
changed" signal. No model reads the page; nothing is claimed that a diff
cannot show.

RFC-060 adds one opt-in per source: `retain` also KEEPS the page's prose as
an org-attributed document, the same shape a company feed post gets, so a
values page becomes something a stance can quote verbatim instead of only
a hash that says it changed. Egress is untouched — same approved pages,
one GET each; only what survives the fetch differs.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field

from wingman.application.company_feeds import ensure_company_anchor
from wingman.application.corpus import extract_document
from wingman.application.ingest import IngestError
from wingman.application.pov import COMPANY_POV_PREFIX, company_card_id
from wingman.application.similarity import company_key
from wingman.domain import SourceRecord
from wingman.domain.person import ExternalDocument
from wingman.domain.research import CompanySource, ResearchSnapshot
from wingman.domain.source_record import derive_document_key
from wingman.infrastructure.config import Config
from wingman.infrastructure.fetch import FetchError, fetch_url
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.research")

# Printed per source in the rendered CLI report only (render_research_report).
# SourceResult.new_links itself carries the FULL diffed list — capping it
# there was the other half of #481: a source with 22 new links in one run
# handed only 10 of them to the caller (focus.py, which filters to "jobish"
# and scores them), and since the snapshot's baseline absorbs every link
# seen regardless, the other 12 were never fetchable-as-new again either.
MAX_NEW_LINKS_SHOWN = 10
# A research snapshot older than this is flagged in the dossier, not hidden.
RESEARCH_STALE_AFTER_DAYS = 30
# A source that has gone this many days without a successful fetch gets an
# escalated failure message (#485) — one bad night reads the same as always,
# but a source stuck failing run after run should not read the same as one
# that just started, or the digest's per-run cap can quietly bury it forever.
RESEARCH_FAILURE_STALE_AFTER_DAYS = 3
# source_type on records and documents kept from an approved research page.
RETAINED_SOURCE_TYPE = "research_page"


class _PageParser(HTMLParser):
    """Visible text and absolute https links of one HTML page, nothing more."""

    _SKIP: ClassVar[set[str]] = {"script", "style", "noscript", "template"}

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base = base_url
        self._skip_depth = 0
        self.text_chunks: list[str] = []
        self.links: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag != "a":
            return
        href = next((value for name, value in attrs if name == "href" and value), None)
        if not href:
            return
        absolute = urljoin(self._base, href.strip()).split("#", 1)[0]
        if absolute.startswith("https://") and absolute not in self._seen:
            self._seen.add(absolute)
            self.links.append(absolute)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.text_chunks.append(data.strip())


def extract_page(data: bytes, base_url: str) -> tuple[str, list[str]]:
    """Deterministic reduction of a page: normalized visible text + https links."""
    parser = _PageParser(base_url)
    parser.feed(data.decode("utf-8", errors="replace"))
    return " ".join(parser.text_chunks), parser.links


class _TitleParser(HTMLParser):
    """The first <title> element's text, nothing more (#109)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_title = False
        self._done = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title" and not self._done:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self._done = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def page_title(data: bytes) -> str | None:
    """The page's <title>, whitespace-collapsed, or None. Deterministic."""
    parser = _TitleParser()
    parser.feed(data.decode("utf-8", errors="replace"))
    title = " ".join(parser.title.split())
    return title or None


def _resolve_company(name: str) -> str:
    key = company_key(name)
    if not key:
        raise IngestError("company name is empty — nothing to research.")
    return key


def canonical_source_url(url: str) -> str:
    """The identity of a research URL, for deciding whether we already have it.

    Approved sources are compared as raw strings, so following a company
    twice with 'https://anthropic.com' and 'https://www.anthropic.com'
    approves both — and every later research run then fetches eight pages
    for four pages of content, against somebody else's servers, with a
    diff that reports every change twice (#348).

    Used ONLY for the identity check. The URL the user supplied is what
    gets stored and fetched, deliberately: plenty of hosts serve only one
    of www/bare, so rewriting what we fetch could turn a working source
    into a 404. Two spellings of the same page should not become two
    sources; that is the whole claim being made here.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url.strip())
    host = parts.hostname or ""
    host = host.removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), host.lower(), path, parts.query, ""))


def _existing_source(key: str, url: str, storage: Storage) -> CompanySource | None:
    wanted = canonical_source_url(url)
    for source in storage.list_company_sources(key):
        if canonical_source_url(source.url) == wanted:
            return source
    return None


def add_company_source(
    name: str,
    url: str,
    storage: Storage,
    label: str | None = None,
    retain: bool | None = None,
) -> tuple[CompanySource, bool]:
    """Approve one research URL for a company. Adding it IS the approval.

    retain is deliberately tri-state (RFC-060): None means "leave retention as
    it is", so re-running add on an already-approved source can never silently
    switch off the retention the user turned on. True/False set it — on a
    source already approved, that is the supported way to change your mind
    without a remove-and-re-add that would throw the snapshot away.
    """
    key = _resolve_company(name)
    url = url.strip()
    if not url.startswith("https://"):
        raise IngestError(f"only https:// research sources are accepted (RFC-009); got {url!r}")
    already = _existing_source(key, url, storage)
    if already is not None:
        # Same page, different spelling — report it as already approved
        # rather than approving it twice (#348). Retention is the one thing
        # a repeat add may still change, and only when asked to.
        if retain is not None and retain != already.retain:
            storage.set_company_source_retention(key, already.url, retain)
            already = already.model_copy(update={"retain": retain})
        return already, False
    source = CompanySource(
        company_key=key,
        company_name=name.strip(),
        url=url,
        label=(label or "").strip() or None,
        retain=bool(retain),
    )
    return source, storage.add_company_source(source)


def pause_company_source(name: str, url: str, storage: Storage, *, paused: bool) -> CompanySource:
    """Stop/resume one source without withdrawing approval or its history."""
    source = _existing_source(_resolve_company(name), url, storage)
    if source is None:
        raise IngestError(f"{url!r} is not an approved source for {name!r}.")
    storage.set_company_source_paused(source.company_key, source.url, paused)
    return source.model_copy(update={"paused": paused})


def remove_company_source(name: str, url: str, storage: Storage) -> bool:
    """Withdraw an approved source: its snapshot AND any retained text go with it.

    Matches the same way approval does (#348): a source approved as
    'https://www.example.com/about' is withdrawn by either spelling, so
    removal is never harder than the addition that created it.

    Withdrawal means the page stops being evidence, not just stops being
    fetched (RFC-060) — leaving its retained prose behind would let a stance
    keep quoting a source the user has revoked.
    """
    key = _resolve_company(name)
    existing = _existing_source(key, url, storage)
    removed = storage.remove_company_source(key, existing.url if existing else url.strip())
    if existing is not None:
        storage.delete_external_documents_for_records(
            storage.record_ids_for_document(derive_document_key(_retained_document_name(existing)))
        )
    return removed


def list_company_sources(name: str, storage: Storage) -> list[CompanySource]:
    return storage.list_company_sources(_resolve_company(name))


def rename_company(old_name: str, new_name: str, storage: Storage) -> tuple[int, int]:
    """Re-key a company's sources, research snapshots, POV card, watchlist
    memberships, AND every person attributed to it (#63 — Person.company is the
    field attribution actually joins on). Returns (sources moved, people moved)."""
    old_key = _resolve_company(old_name)
    new_key = _resolve_company(new_name)
    if old_key == new_key:
        raise IngestError("new name normalizes to the same company — nothing to rename.")
    moved = storage.move_company_sources(old_key, new_key, new_name.strip())
    storage.move_pov_card(company_card_id(old_key), company_card_id(new_key))
    # The open-web deep dive (#350) is keyed by company too — left behind, it
    # would be unreachable under the new name and still returned under the old.
    storage.move_company_dossier(old_key, new_key, new_name.strip())
    storage.watchlist_rename_member("company", old_name.strip(), new_name.strip())
    # The company feed anchor (RFC-029) is keyed by company: re-key it and
    # carry its documents/news along, before the general people loop.
    anchor = storage.get_person(company_card_id(old_key))
    if anchor is not None:
        rekeyed = anchor.model_copy(
            update={
                "person_id": company_card_id(new_key),
                "name": f"{new_name.strip()} (company)",
                "company": new_name.strip(),
            }
        )
        storage.add_person(rekeyed)
        storage.move_person_content(anchor.person_id, rekeyed.person_id)
        storage.delete_person(anchor.person_id)
    people_moved = 0
    for person in storage.list_people():
        if person.person_id.startswith(COMPANY_POV_PREFIX):
            continue  # anchors were re-keyed above
        if person.company and company_key(person.company) == old_key:
            storage.update_person(person.model_copy(update={"company": new_name.strip()}))
            people_moved += 1
    return moved, people_moved


def delete_company(name: str, storage: Storage) -> tuple[bool, list[str]]:
    """Delete a company's approved sources, research snapshots, POV card, open-web
    deep dive, and any watchlist memberships under this exact name — and clear Person.company on
    everyone attributed to it, so the delete is actually complete (#64).
    Returns (whether anything existed, names of people whose company was cleared)."""
    key = _resolve_company(name)
    removed_sources = storage.delete_company_sources(key) > 0
    removed_card = storage.delete_pov_card(company_card_id(key))
    removed_dossier = storage.delete_company_dossier(key)
    removed_watchlist = storage.watchlist_delete_member("company", name.strip()) > 0
    # The feed anchor and everything keyed to it goes with the company (RFC-029).
    removed_anchor = storage.delete_person(company_card_id(key))
    cleared: list[str] = []
    for person in storage.list_people():
        if person.company and company_key(person.company) == key:
            storage.update_person(person.model_copy(update={"company": None}))
            cleared.append(person.name)
    existed = (
        removed_sources or removed_card or removed_dossier or removed_watchlist or removed_anchor
    )
    return existed or bool(cleared), cleared


def _retained_document_name(source: CompanySource) -> str:
    """The archive filename whose basename IS this source's document key.

    RFC-028 lineage keys a document by its archive basename, so the name must
    be stable across fetches of the same page and distinct between pages. It
    is derived from the URL alone — never the company — so that renaming a
    company (which re-keys its sources) cannot orphan the lineage and make
    the next fetch pile up a second copy instead of superseding the first.
    The URL is canonicalized first (#348), so the same page under two
    spellings is one document. The readable stem is for humans browsing the
    inbox; the digest is what guarantees two pages never collide.
    """
    canonical = canonical_source_url(source.url)
    parts = urlsplit(canonical)
    stem = re.sub(r"[^a-z0-9]+", "-", f"{parts.netloc}{parts.path}".lower()).strip("-")
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"research-{stem[:60] or 'page'}-{digest}.html"


def _archive_locator(path: Path, config: Config) -> str:
    resolved = config.data_dir.resolve()
    return str(path.relative_to(resolved)) if path.is_relative_to(resolved) else str(path)


def _retain_page(
    source: CompanySource,
    data: bytes,
    text_unchanged: bool,
    config: Config,
    storage: Storage,
) -> str:
    """Keep this page's prose as a company-attributed document (RFC-060).

    Returns a one-word status for the report. The body is extracted with the
    same extractor feed posts use, NOT the snapshot pipeline's visible text:
    that text is joined across every tag boundary, so '<a>Anthro</a>pic'
    becomes 'Anthro pic' and the verbatim-quote gate rejects the sentence the
    page actually contains. Hashing does not care; quoting does.
    """
    if text_unchanged and storage.has_external_url(source.url):
        return "unchanged"
    raw = data.decode("utf-8", errors="replace")
    fallback = source.label or source.url
    title, body = extract_document(raw, fallback, ".html")
    if not body.strip():
        return "no text"
    name = _retained_document_name(source)
    document_key = derive_document_key(name)
    content_hash = hashlib.sha256(data).hexdigest()
    record = storage.get_source_record_by_hash(content_hash)
    # Reuse only a record of THIS document: a byte-identical page archived by
    # some other path is not a version of this source's lineage.
    if record is not None and record.document_key != document_key:
        record = None
    if record is not None and storage.find_external_document_by_source(record.record_id):
        return "unchanged"
    if record is None:
        config.inbox_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        archived = config.inbox_dir / f"{stamp}-{name}"
        archived.write_text(raw, encoding="utf-8")
        record = SourceRecord(
            source_type=RETAINED_SOURCE_TYPE,
            source_locator=_archive_locator(archived, config),
            content_hash=content_hash,
            document_key=document_key,
        )
        storage.add_source_record(record)
    superseded = storage.record_ids_for_document(document_key, exclude_record_id=record.record_id)
    anchor = ensure_company_anchor(source.company_name, storage)
    storage.add_external_document(
        ExternalDocument(
            source_record_id=record.record_id,
            person_id=anchor.person_id,
            source_type=RETAINED_SOURCE_TYPE,
            title=title,
            url=source.url,
            organization=anchor.company or source.company_name,
            # Deliberately undated: a fetch tells us when we LOOKED, never
            # when the page was written, and stamping the fetch time onto
            # published_at would be an invented fact (AGENTS.md invariant 8).
            published_at=None,
            word_count=len(body.split()),
        ),
        body,
    )
    replaced = storage.delete_external_documents_for_records(superseded)
    return "replaced" if replaced else "stored"


class SourceResult(BaseModel):
    url: str
    label: str | None = None
    status: str  # ok | failed | paused
    detail: str
    new_links: list[str] = Field(default_factory=list)
    total_links: int = 0
    # '' when the source is not retained; else stored | replaced | unchanged | no text.
    retained: str = ""


class ResearchReport(BaseModel):
    company: str
    results: list[SourceResult]
    fetched: int
    failed: int


def research_company(
    name: str,
    config: Config,
    storage: Storage,
    fetcher: Callable[[str], bytes] | None = None,
) -> ResearchReport:
    """Fetch every approved source once and report what changed since last time.

    One GET per approved URL — the approval happened at add-source time. A
    failed source keeps its previous snapshot and is reported, never fatal.

    A source marked `retain` (RFC-060) additionally keeps the page's prose as
    an org-attributed document. That changes nothing about the fetch: same
    pages, same one GET each, same snapshot and diff.
    """
    key = _resolve_company(name)
    sources = storage.list_company_sources(key)
    if not sources:
        raise IngestError(
            f"no approved research sources for {name!r}. Approve one with "
            f"'wingman company add-source \"{name}\" <https-url>'."
        )
    fetch = fetcher if fetcher is not None else fetch_url
    results: list[SourceResult] = []
    failed = 0
    kept = 0
    for source in sources:
        if source.paused:
            results.append(
                SourceResult(
                    url=source.url,
                    label=source.label,
                    status="paused",
                    detail="paused; previous research preserved",
                )
            )
            continue
        try:
            data = fetch(source.url)
        except FetchError as exc:
            failed += 1
            detail = f"fetch failed: {exc}. The previous snapshot was kept."
            previous = storage.get_research_snapshot(key, source.url)
            reference = previous.fetched_at if previous is not None else source.added_at
            age_days = (datetime.now(UTC) - reference).days
            if age_days >= RESEARCH_FAILURE_STALE_AFTER_DAYS:
                since = reference.date().isoformat()
                what = "the last successful fetch" if previous is not None else "it was approved"
                detail += (
                    f" ⚠ This source has not fetched successfully in {age_days} day(s), "
                    f"since {what} on {since} — check the source or the fetch mechanism, "
                    "not just tonight's run."
                )
            results.append(
                SourceResult(
                    url=source.url,
                    label=source.label,
                    status="failed",
                    detail=detail,
                )
            )
            continue
        text, links = extract_page(data, source.url)
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        previous = storage.get_research_snapshot(key, source.url)
        fetched_at = datetime.now(UTC)
        if previous is None:
            detail = f"first snapshot: {len(links)} links recorded as the baseline"
            new_links: list[str] = []
        else:
            known = set(previous.links)
            new_links = [link for link in links if link not in known]
            since = previous.fetched_at.date().isoformat()
            if new_links:
                noun = "link" if len(new_links) == 1 else "links"
                detail = f"{len(new_links)} new {noun} since {since}"
            elif text_hash != previous.text_hash:
                detail = f"page text changed since {since} (no new links)"
            else:
                detail = f"unchanged since {since}"
        storage.save_research_snapshot(
            ResearchSnapshot(
                company_key=key,
                url=source.url,
                text_hash=text_hash,
                links=links,
                fetched_at=fetched_at,
            )
        )
        storage.record_new_links(key, source.url, new_links, fetched_at)
        retained = ""
        if source.retain:
            retained = _retain_page(
                source,
                data,
                text_unchanged=previous is not None and text_hash == previous.text_hash,
                config=config,
                storage=storage,
            )
            if retained in {"stored", "replaced"}:
                kept += 1
        results.append(
            SourceResult(
                url=source.url,
                label=source.label,
                status="ok",
                detail=detail,
                new_links=new_links,
                total_links=len(links),
                retained=retained,
            )
        )
    _logger.info(
        "research company=%s sources=%d failed=%d retained=%d", name, len(sources), failed, kept
    )
    return ResearchReport(
        company=name.strip(),
        results=results,
        fetched=sum(result.status == "ok" for result in results),
        failed=failed,
    )


_RETAINED_DETAIL = {
    "stored": "kept as a document attributed to the company — quotable now",
    "replaced": "kept as a document, superseding the previous version (RFC-028)",
    "unchanged": "already kept; the page text has not changed",
    "no text": "nothing extractable to keep from this page",
}


def render_research_report(report: ResearchReport) -> str:
    lines = [f"Research: {report.company} — {report.fetched} fetched, {report.failed} failed"]
    for result in report.results:
        label = f" ({result.label})" if result.label else ""
        marker = {"ok": "✓", "paused": "⏸"}.get(result.status, "✗")
        lines.append(f"{marker} {result.url}{label}")
        lines.append(f"  {result.detail}")
        shown = result.new_links[:MAX_NEW_LINKS_SHOWN]
        lines.extend(f"  + {link}" for link in shown)
        hidden = len(result.new_links) - len(shown)
        if hidden:
            lines.append(f"  (+{hidden} more not shown)")
        if result.retained:
            lines.append(f"  retained text: {_RETAINED_DETAIL[result.retained]}")
    return "\n".join(lines)
