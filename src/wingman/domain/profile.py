"""Career profile entities: achievements and skills with evidence references."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

from wingman.domain.provenance import ClaimClassification


class ProfileItemKind(StrEnum):
    ACHIEVEMENT = "achievement"
    SKILL = "skill"
    ROLE = "role"
    TESTIMONIAL = "testimonial"


class ItemStatus(StrEnum):
    ACTIVE = "active"
    CONFLICT = "conflict"


class EvidenceSpan(BaseModel):
    """A verbatim quote from a source record backing a claim."""

    source_record_id: str
    quote: str


class ProfileItem(BaseModel):
    """One achievement or skill in the canonical profile.

    Every item carries at least one evidence span; nothing enters the profile
    without a resolvable evidence reference.
    """

    item_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: ProfileItemKind
    name: str
    detail: str = ""
    classification: ClaimClassification
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceSpan] = Field(min_length=1)
    status: ItemStatus = ItemStatus.ACTIVE
    conflicts_with: str | None = None
    prompt_version: str
    extracted_by: str
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def name_key(self) -> str:
        """Deterministic key for deduplication and conflict detection."""
        return " ".join(self.name.lower().split())
