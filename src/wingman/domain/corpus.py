"""Corpus entities: the user's writing as citable evidence sources."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class CorpusDocument(BaseModel):
    """One imported document (essay, README, export entry) backed by a SourceRecord.

    The raw artifact and its content hash live on the SourceRecord; the corpus
    document carries the extracted plain text's metadata and is the unit of
    search and quotation.
    """

    doc_id: str = Field(default_factory=lambda: str(uuid4()))
    source_record_id: str
    source_type: str
    title: str
    url: str | None = None
    published_at: datetime | None = None
    word_count: int
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EvidenceHit(BaseModel):
    """One search result: a document plus the matching excerpt."""

    document: CorpusDocument
    snippet: str
    source_locator: str
