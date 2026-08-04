"""Career profile entities: achievements, skills, roles, and testimonials with evidence."""

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
    # A reaction captured via the interview mechanic (docs/PROFILE-BOOTSTRAP-
    # DESIGN.md): one kind for every interview category, differentiated by
    # ProfileItem.subtype rather than adding a kind per category/reaction.
    INTERVIEW = "interview"


class ItemStatus(StrEnum):
    ACTIVE = "active"
    CONFLICT = "conflict"
    # Replaced by a newer version of the same source document (RFC-028):
    # kept for provenance (old assessments cite it), invisible everywhere else.
    SUPERSEDED = "superseded"


class SentimentIntensity(StrEnum):
    """How strongly a Values/Mission-alignment nomination is felt
    (RFC-049, issue #240 v1) — layered ON TOP OF the subtype's own
    pro/con polarity (`_pro`/`_con`), never a substitute for it. The
    user's own scale choice, captured verbatim, never inferred — same
    "never inferred, always the user's own call" spirit as `HeapHeat`.
    Combined with the subtype (`_pro` already means positive, `_con`
    already means negative) this produces the full strongly-positive to
    strongly-negative range without a second, independently-settable
    polarity field that could silently disagree with the one the subtype
    already decided.
    """

    MILD = "mild"
    MODERATE = "moderate"
    STRONG = "strong"


class CompanyReasonCategory(StrEnum):
    """Why a company-naming nomination is interesting, against a small
    fixed taxonomy (RFC-049, issue #240 v1) — captured ALONGSIDE the
    free-text `why`, never replacing it. Scoped to subtypes that name a
    company (`values_fallback_pro/con`, `mission_alignment_pro/con`);
    people-naming subtypes (`values_pro/con`, `network_admired`) have no
    company to categorize.
    """

    COMPANY = "company"  # the company itself — leadership, culture, actions
    PRODUCT = "product"  # what it makes or sells
    INDUSTRY = "industry"  # the industry/sector it operates in


class EvidenceSpan(BaseModel):
    """A verbatim quote from a source record backing a claim."""

    source_record_id: str
    quote: str


class ProfileItem(BaseModel):
    """One item in the canonical profile: an achievement, skill, role, or testimonial.

    Every item carries at least one evidence span; nothing enters the profile
    without a resolvable evidence reference.
    """

    item_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: ProfileItemKind
    subtype: str | None = None
    # None = the coach's own item (every item in every workspace today is
    # implicitly this — zero migration needed). Set = scoped to that
    # Persona (docs/COACHING-MODE-DESIGN.md) — "my evidence and their
    # point of view never mix" (domain/person.py's own invariant) extended
    # to a second axis: coach vs. persona, and persona vs. persona.
    persona_id: str | None = None
    name: str
    detail: str = ""
    classification: ClaimClassification
    confidence: float = Field(ge=0.0, le=1.0)
    # Interview-nomination scale fields (RFC-049, issue #240 v1) — None for
    # every non-nomination kind, and for the interview subtypes outside
    # this slice's scope (alignment_of_perspective_*, network_admired).
    # See application/interview.py's subtype sets for exactly which
    # subtypes populate which field.
    intensity: SentimentIntensity | None = None
    company_reason: CompanyReasonCategory | None = None
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
