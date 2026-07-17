"""SourceRecord: the immutable root of every provenance chain."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class SourceRecord(BaseModel):
    """An imported artifact (resume, job description, export, saved page).

    The raw bytes stay on disk in the workspace inbox; the record stores their
    hash and locator. Immutable once ingested — corrections create new records.
    """

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(default_factory=lambda: str(uuid4()))
    source_type: str
    source_locator: str
    content_hash: str
    source_timestamp: datetime | None = None
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
