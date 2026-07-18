"""Recent news about a person and their company, via a public news RSS feed.

The one search-shaped fetch in Wingman, kept inside the RFC-009 invariants:
explicit user invocation, one read-only HTTPS GET of a public
unauthenticated endpoint (Google News's RSS search), visible failures.

Privacy, stated plainly: the query — the person's name and their company —
is sent to the news provider when you run this. That is the entire egress;
nothing else about the workspace leaves the machine.
"""

from __future__ import annotations

from collections.abc import Callable
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


class NewsReport(BaseModel):
    person_name: str
    query: str
    stored: int
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
) -> NewsReport:
    """Fetch and store the current news snapshot for a person (replaces the old one).

    One GET of the public news RSS endpoint. The query (their name and
    company) is sent to the provider — that is this command's data egress.
    """
    from wingman.application.people import _parse_feed_items, _published_at

    fetch = fetcher if fetcher is not None else fetch_url
    query = build_news_query(person)
    url = f"{_GOOGLE_NEWS_RSS}?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        data = fetch(url)
    except FetchError as exc:
        raise IngestError(f"news fetch failed: {exc}. The stored snapshot was kept.") from exc
    entries = _parse_feed_items(data, url)
    items: list[NewsItem] = []
    for entry in entries:
        title = entry["title"].strip()
        link = entry["link"].strip()
        if not title or not link.startswith("https://"):
            continue
        items.append(
            NewsItem(
                person_id=person.person_id,
                title=title,
                url=link,
                published_at=_published_at(entry["published"]),
            )
        )
        if len(items) >= NEWS_LIMIT:
            break
    storage.replace_person_news(person.person_id, items)
    _logger.info("news person=%s query=%s stored=%d", person.name, query, len(items))
    return NewsReport(
        person_name=person.name,
        query=query,
        stored=len(items),
        titles=[item.title for item in items],
    )
