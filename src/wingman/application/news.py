"""Recent news about a person and their company, via a public news RSS feed.

The one search-shaped fetch in Wingman, kept inside the RFC-009 invariants:
explicit user invocation, one read-only HTTPS GET of a public
unauthenticated endpoint (Google News's RSS search), visible failures.

Privacy, stated plainly: the query — the person's name and their company —
is sent to the news provider when you run this. That is the entire egress;
nothing else about the workspace leaves the machine.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from pydantic import BaseModel, Field

from wingman.application.ingest import IngestError
from wingman.domain.person import NewsItem, Person
from wingman.infrastructure.fetch import FetchError, fetch_url
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.news")

NEWS_LIMIT = 8
_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
# Google News' RSS search keeps returning the same evergreen/popular result
# for a sparse query indefinitely — a real 2024 funding story showed up in
# a 2026 digest looking exactly like fresh news (issue #142). A dated item
# older than this is dropped rather than presented as current; an UNDATED
# item is kept — we can't tell if it's stale, and guessing "old" is no more
# honest than guessing "new".
STALE_AFTER_DAYS = 180

# Company names are often ordinary words ("Supersimple" the startup vs
# "supersimple trick" the adjective). A company-matched headline only counts
# when it also carries a corporate-context word; person-name matches always
# count. Deterministic and explainable — dropped items are tallied, never
# silently vanished.
_CORPORATE_CONTEXT = frozenset(
    {
        "raises",
        "raised",
        "raise",
        "funding",
        "round",
        "seed",
        "series",
        "launches",
        "launch",
        "launched",
        "announces",
        "announced",
        "unveils",
        "acquires",
        "acquired",
        "acquisition",
        "merges",
        "merger",
        "partners",
        "partnership",
        "ceo",
        "cto",
        "founder",
        "founders",
        "startup",
        "million",
        "billion",
        "hires",
        "appoints",
        "names",
        "valuation",
        "ipo",
        "revenue",
        "customers",
        "ai",
    }
)


def _title_is_relevant(title: str, person: Person) -> bool:
    lowered = title.lower()
    if person.name.strip() and person.name.lower() in lowered:
        return True
    company = (person.company or "").strip().lower()
    if not company or company not in lowered:
        return False
    words = set(re.findall(r"[a-z0-9]+", lowered))
    return not _CORPORATE_CONTEXT.isdisjoint(words)


class NewsReport(BaseModel):
    person_name: str
    query: str
    stored: int
    dropped: int = 0
    dropped_stale: int = 0
    titles: list[str] = Field(default_factory=list)


def build_news_query(person: Person) -> str:
    """Exact-phrase query for the person, OR their company when known."""
    terms = [f'"{person.name}"']
    if person.company:
        terms.append(f'"{person.company}"')
    return " OR ".join(terms)


def fetch_person_news(
    person: Person,
    storage: Storage,
    fetcher: Callable[[str], bytes] | None = None,
    now: datetime | None = None,
) -> NewsReport:
    """Fetch and store the current news snapshot for a person (replaces the old one).

    One GET of the public news RSS endpoint. The query (their name and
    company) is sent to the provider — that is this command's data egress.
    """
    from wingman.application.people import _parse_feed_items, _published_at

    fetch = fetcher if fetcher is not None else fetch_url
    moment = now if now is not None else datetime.now(UTC)
    stale_cutoff = moment - timedelta(days=STALE_AFTER_DAYS)
    query = build_news_query(person)
    url = f"{_GOOGLE_NEWS_RSS}?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        data = fetch(url)
    except FetchError as exc:
        raise IngestError(f"news fetch failed: {exc}. The stored snapshot was kept.") from exc
    entries = _parse_feed_items(data, url)
    kept: list[NewsItem] = []
    dropped = 0
    dropped_stale = 0
    for entry in entries:
        title = entry["title"].strip()
        link = entry["link"].strip()
        if not title or not link.startswith("https://"):
            continue
        if not _title_is_relevant(title, person):
            dropped += 1
            continue
        published_at = _published_at(entry["published"])
        if published_at is not None and published_at < stale_cutoff:
            dropped_stale += 1
            continue
        kept.append(
            NewsItem(
                person_id=person.person_id,
                title=title,
                url=link,
                published_at=published_at,
            )
        )
    # newest first, undated last, then cap — a briefing wants recency
    kept.sort(
        key=lambda item: (
            item.published_at is not None,
            item.published_at.timestamp() if item.published_at else 0.0,
        ),
        reverse=True,
    )
    items = kept[:NEWS_LIMIT]
    storage.replace_person_news(person.person_id, items)
    _logger.info(
        "news person=%s query=%s stored=%d dropped=%d dropped_stale=%d",
        person.name,
        query,
        len(items),
        dropped,
        dropped_stale,
    )
    return NewsReport(
        person_name=person.name,
        query=query,
        stored=len(items),
        dropped=dropped,
        dropped_stale=dropped_stale,
        titles=[item.title for item in items],
    )
