"""People entities: the watchlist of humans whose thinking the user follows.

A Person is a relationship record built from explicit imports (a LinkedIn
connections export) or manual additions — never from scraping. Their public
writing is stored as ExternalDocuments: the same shape as the user's own
CorpusDocuments but attributed to a person and kept in a separate index, so
"my evidence" and "their point of view" never mix.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class PersonOrigin(StrEnum):
    MANUAL = "manual"
    LINKEDIN_CONNECTIONS = "linkedin_connections"


class FeedKind(StrEnum):
    RSS = "rss"  # RSS 2.0 or Atom — parsed by format, one name for both
    INDEX_PAGE = "index_page"  # a blog index page on a site with no feed (RFC-011)


class FeedAttribution(StrEnum):
    PERSON = "person"
    ORGANIZATION = "organization"


class FeedSource(BaseModel):
    """One public source of a person's writing (RFC-011).

    attribution=organization marks sources like a company blog, where posts
    are honestly attributed to the organization (org_name) rather than
    presented as the person's own byline.
    """

    url: str
    kind: FeedKind = FeedKind.RSS
    attribution: FeedAttribution = FeedAttribution.PERSON
    org_name: str | None = None


class Person(BaseModel):
    """One watched or known person; identified by a normalized name key."""

    person_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1)
    origin: PersonOrigin
    substack_url: str | None = None
    writing_feed_url: str | None = None
    writing_feed_kind: FeedKind = FeedKind.RSS
    feeds: list[FeedSource] = Field(default_factory=list)
    linkedin_url: str | None = None
    # Only ever set manually ('wingman people add --email'): imports never
    # read email addresses (the Connections.csv privacy decision stands).
    email: str | None = None
    company: str | None = None
    position: str | None = None
    connected_on: str | None = None
    source_record_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def name_key(self) -> str:
        return " ".join(self.name.lower().split())

    @property
    def sources(self) -> list[FeedSource]:
        """The discovered writing feed (or unresolved entered URL), plus added feeds."""
        configured: list[FeedSource] = []
        if self.substack_url:
            configured.append(
                FeedSource(
                    url=self.writing_feed_url or self.substack_url, kind=self.writing_feed_kind
                )
            )
        configured.extend(self.feeds)
        return configured


class ExternalDocument(BaseModel):
    """One public document by a watched person, backed by a SourceRecord.

    organization is set when the document is honestly attributed to an
    organization's outlet (a company blog) rather than the person's own
    byline (RFC-011).
    """

    doc_id: str = Field(default_factory=lambda: str(uuid4()))
    source_record_id: str
    person_id: str
    source_type: str
    title: str
    url: str | None = None
    organization: str | None = None
    published_at: datetime | None = None
    word_count: int
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NewsItem(BaseModel):
    """One recent news mention of a person or their company, from a public
    news feed (never their own writing — that's ExternalDocument)."""

    item_id: str = Field(default_factory=lambda: str(uuid4()))
    person_id: str
    title: str
    url: str
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PersonDossier(BaseModel):
    """A one-shot open-web research dossier for a person (#222).

    Unlike PovCard/OutreachBrief, there's no locally stored document to
    verify a quote against — trust rests on the provider's own citation
    metadata (OpenRouterProvider's 'Sources:' block), not a verbatim-match
    gate. Free text, not a structured/validated record. Rebuilt on demand,
    same as a POV card — one dossier per person, not versioned.
    """

    dossier_id: str = Field(default_factory=lambda: str(uuid4()))
    person_id: str
    person_name: str
    content: str = Field(min_length=1)
    provider: str = ""
    model: str = ""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExternalEvidenceHit(BaseModel):
    """One search result over people's writing: document, excerpt, and author."""

    document: ExternalDocument
    snippet: str
    person_name: str
