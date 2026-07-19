"""Company research over user-approved sources (RFC-015).

The network-scope decision RFC-012 deferred, resolved: the user names the
exact pages they trust for a company (a careers page, a newsroom), and
`wingman company research` makes one explicit, read-only HTTPS GET per
approved source (RFC-009 shape). What comes back is reduced to a
deterministic snapshot — a hash of the page's visible text and its set of
links — and the finding is the diff against the previous snapshot: new
links are the hiring/announcement signal, a changed hash the weaker "page
changed" signal. No model reads the page; nothing is claimed that a diff
cannot show.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import urljoin

from pydantic import BaseModel, Field

from wingman.application.ingest import IngestError
from wingman.application.pov import company_card_id
from wingman.application.similarity import company_key
from wingman.domain.research import CompanySource, ResearchSnapshot
from wingman.infrastructure.fetch import FetchError, fetch_url
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.research")

# Shown per source in reports; the stored snapshot keeps the full link set.
MAX_NEW_LINKS_SHOWN = 10
# A research snapshot older than this is flagged in the dossier, not hidden.
RESEARCH_STALE_AFTER_DAYS = 30


class _PageParser(HTMLParser):
    """Visible text and absolute https links of one HTML page, nothing more."""

    _SKIP = {"script", "style", "noscript", "template"}

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


def _resolve_company(name: str) -> str:
    key = company_key(name)
    if not key:
        raise IngestError("company name is empty — nothing to research.")
    return key


def add_company_source(
    name: str, url: str, storage: Storage, label: str | None = None
) -> tuple[CompanySource, bool]:
    """Approve one research URL for a company. Adding it IS the approval."""
    key = _resolve_company(name)
    url = url.strip()
    if not url.startswith("https://"):
        raise IngestError(f"only https:// research sources are accepted (RFC-009); got {url!r}")
    source = CompanySource(
        company_key=key, company_name=name.strip(), url=url, label=(label or "").strip() or None
    )
    created = storage.add_company_source(source)
    return source, created


def remove_company_source(name: str, url: str, storage: Storage) -> bool:
    """Withdraw an approved source (and its stored snapshot)."""
    return storage.remove_company_source(_resolve_company(name), url.strip())


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
    storage.watchlist_rename_member("company", old_name.strip(), new_name.strip())
    people_moved = 0
    for person in storage.list_people():
        if person.company and company_key(person.company) == old_key:
            storage.update_person(person.model_copy(update={"company": new_name.strip()}))
            people_moved += 1
    return moved, people_moved


def delete_company(name: str, storage: Storage) -> tuple[bool, list[str]]:
    """Delete a company's approved sources, research snapshots, POV card, and any
    watchlist memberships under this exact name — and clear Person.company on
    everyone attributed to it, so the delete is actually complete (#64).
    Returns (whether anything existed, names of people whose company was cleared)."""
    key = _resolve_company(name)
    removed_sources = storage.delete_company_sources(key) > 0
    removed_card = storage.delete_pov_card(company_card_id(key))
    removed_watchlist = storage.watchlist_delete_member("company", name.strip()) > 0
    cleared: list[str] = []
    for person in storage.list_people():
        if person.company and company_key(person.company) == key:
            storage.update_person(person.model_copy(update={"company": None}))
            cleared.append(person.name)
    return removed_sources or removed_card or removed_watchlist or bool(cleared), cleared


class SourceResult(BaseModel):
    url: str
    label: str | None = None
    status: str  # ok | failed
    detail: str
    new_links: list[str] = Field(default_factory=list)
    total_links: int = 0


class ResearchReport(BaseModel):
    company: str
    results: list[SourceResult]
    fetched: int
    failed: int


def research_company(
    name: str,
    storage: Storage,
    fetcher: Callable[[str], bytes] | None = None,
) -> ResearchReport:
    """Fetch every approved source once and report what changed since last time.

    One GET per approved URL — the approval happened at add-source time. A
    failed source keeps its previous snapshot and is reported, never fatal.
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
    for source in sources:
        try:
            data = fetch(source.url)
        except FetchError as exc:
            failed += 1
            results.append(
                SourceResult(
                    url=source.url,
                    label=source.label,
                    status="failed",
                    detail=f"fetch failed: {exc}. The previous snapshot was kept.",
                )
            )
            continue
        text, links = extract_page(data, source.url)
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        previous = storage.get_research_snapshot(key, source.url)
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
            ResearchSnapshot(company_key=key, url=source.url, text_hash=text_hash, links=links)
        )
        results.append(
            SourceResult(
                url=source.url,
                label=source.label,
                status="ok",
                detail=detail,
                new_links=new_links[:MAX_NEW_LINKS_SHOWN],
                total_links=len(links),
            )
        )
    _logger.info("research company=%s sources=%d failed=%d", name, len(sources), failed)
    return ResearchReport(
        company=name.strip(), results=results, fetched=len(sources) - failed, failed=failed
    )


def render_research_report(report: ResearchReport) -> str:
    lines = [f"Research: {report.company} — {report.fetched} fetched, {report.failed} failed"]
    for result in report.results:
        label = f" ({result.label})" if result.label else ""
        marker = "✓" if result.status == "ok" else "✗"
        lines.append(f"{marker} {result.url}{label}")
        lines.append(f"  {result.detail}")
        lines.extend(f"  + {link}" for link in result.new_links)
    return "\n".join(lines)
