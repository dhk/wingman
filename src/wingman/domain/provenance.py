"""Provenance metadata carried by every record that can influence a recommendation (RFC-005)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

# How an answer reached the workspace when it did NOT arrive in a
# conversation (#287): the person answered wingman's own interview
# questions in a form, offline, and an operator ingested the export into
# their workspace. One sentence, defined once, because a reader can meet it
# in three different places — an inbox note, an answer-bank entry's
# context, and the assembled job-criteria document — and three hand-written
# variants would read as three different mechanisms.
FORM_ARRIVAL = "the wingman interview form, ingested by the operator"

#: What `ProfileItem.extracted_by` says for a capture that arrived that
#: way — one value shared by every capture path a form can reach
#: (nominations and Q&A alike), so a surface that wants to label form
#: answers matches one constant rather than a list that can fall behind.
FORM_EXTRACTOR = "form"


class ClaimClassification(StrEnum):
    """How strongly a value is supported by its sources."""

    FACT = "fact"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"


class TransformationStep(BaseModel):
    """One appended entry in a record's transformation history."""

    description: str
    performed_by: str
    performed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserOverride(BaseModel):
    """A user's explicit correction of a derived value."""

    field_name: str
    overridden_value: str
    reason: str | None = None
    overridden_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Provenance(BaseModel):
    """The full RFC-005 provenance schema.

    Transformations are append-only: derive a new model with an extended list
    rather than mutating history in place.
    """

    record_id: str = Field(default_factory=lambda: str(uuid4()))
    source_type: str
    source_locator: str
    source_timestamp: datetime | None = None
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    content_hash: str | None = None
    transformations: list[TransformationStep] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    classification: ClaimClassification
    user_overrides: list[UserOverride] = Field(default_factory=list)
