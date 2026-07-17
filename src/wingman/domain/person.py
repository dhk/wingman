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


class Person(BaseModel):
    """One watched or known person; identified by a normalized name key."""

    person_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1)
    origin: PersonOrigin
    substack_url: str | None = None
    linkedin_url: str | None = None
    company: str | None = None
    position: str | None = None
    connected_on: str | None = None
    source_record_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def name_key(self) -> str:
        return " ".join(self.name.lower().split())


class ExternalDocument(BaseModel):
    """One public document by a watched person, backed by a SourceRecord."""

    doc_id: str = Field(default_factory=lambda: str(uuid4()))
    source_record_id: str
    person_id: str
    source_type: str
    title: str
    url: str | None = None
    published_at: datetime | None = None
    word_count: int
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExternalEvidenceHit(BaseModel):
    """One search result over people's writing: document, excerpt, and author."""

    document: ExternalDocument
    snippet: str
    person_name: str
