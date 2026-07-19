"""Company-attached feeds: follow a company's blog with no person in the loop (RFC-029).

Found in live use: feeds were person-keyed throughout, so attaching a
company newsroom meant inventing a placeholder human. The fix formalizes
the placeholder as a system-owned **company anchor** — a Person row whose
id is the company's `__company__{key}` (the same reserved namespace
company POV cards already use, RFC-016), invisible in people listings and
managed only through the company surface. Every feed attached through it
is organization-attributed, so provenance stays honest: posts belong to
the company, not to a fictional byline. The entire person-keyed pipeline
— fetch, dedupe, external documents, news, themes, dossiers, search —
works unchanged on top.
"""

from __future__ import annotations

from collections.abc import Callable

from wingman.application.ingest import IngestError
from wingman.application.people import FeedFetchReport, attach_feed, fetch_person_feed
from wingman.application.pov import COMPANY_POV_PREFIX, company_card_id
from wingman.application.similarity import company_key
from wingman.domain.person import (
    FeedAttribution,
    FeedKind,
    FeedSource,
    Person,
    PersonOrigin,
)
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.company_feeds")

ANCHOR_SUFFIX = " (company)"


def is_company_anchor(person: Person) -> bool:
    return person.person_id.startswith(COMPANY_POV_PREFIX)


def get_company_anchor(company: str, storage: Storage) -> Person | None:
    key = company_key(company)
    if not key:
        return None
    return storage.get_person(company_card_id(key))


def ensure_company_anchor(company: str, storage: Storage) -> Person:
    """Get or create the anchor that holds a company's own feeds."""
    name = company.strip()
    key = company_key(name)
    if not key:
        raise IngestError("company name is empty; nothing to attach to.")
    existing = get_company_anchor(name, storage)
    if existing is not None:
        return existing
    anchor = Person(
        person_id=company_card_id(key),
        name=f"{name}{ANCHOR_SUFFIX}",
        origin=PersonOrigin.MANUAL,
        company=name,
    )
    storage.add_person(anchor)
    _logger.info("company anchor created company=%s id=%s", name, anchor.person_id)
    return anchor


def attach_company_feed(
    company: str, url: str, storage: Storage, index_page: bool = False
) -> tuple[Person, FeedSource]:
    """Attach a feed straight to a company — organization-attributed, no human byline."""
    anchor = ensure_company_anchor(company, storage)
    source = FeedSource(
        url=url,
        kind=FeedKind.INDEX_PAGE if index_page else FeedKind.RSS,
        attribution=FeedAttribution.ORGANIZATION,
        org_name=anchor.company or company.strip(),
    )
    updated = attach_feed(anchor, source, storage)
    return updated, updated.feeds[-1]  # the stored, URL-normalized copy


def list_company_feeds(company: str, storage: Storage) -> list[FeedSource]:
    anchor = get_company_anchor(company, storage)
    return list(anchor.sources) if anchor is not None else []


def remove_company_feed(company: str, url: str, storage: Storage) -> bool:
    anchor = get_company_anchor(company, storage)
    if anchor is None:
        return False
    normalized = url.rstrip("/")
    kept = [feed for feed in anchor.feeds if feed.url.rstrip("/") != normalized]
    if len(kept) == len(anchor.feeds):
        return False
    storage.update_person(anchor.model_copy(update={"feeds": kept}))
    return True


def fetch_company_feeds(
    company: str,
    config: Config,
    storage: Storage,
    fetcher: Callable[[str], bytes] | None = None,
) -> FeedFetchReport:
    """Fetch every feed attached to the company (RFC-009: explicit, enumerable)."""
    anchor = get_company_anchor(company, storage)
    if anchor is None or not anchor.sources:
        raise IngestError(
            f"{company.strip() or company!r} has no company feeds. Attach one with "
            f"'wingman company add-feed \"{company.strip()}\" <url>'."
        )
    return fetch_person_feed(anchor, config, storage, fetcher=fetcher)
