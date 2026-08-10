"""The schemas model outputs must satisfy before deterministic validation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from wingman.domain.opportunity import FitVerdict, RequirementKind
from wingman.domain.profile import ProfileItemKind
from wingman.domain.provenance import ClaimClassification

# The kinds a document extraction may propose. Deliberately NOT the whole
# of ProfileItemKind: INTERVIEW items carry a subtype and a persona scope
# (docs/COACHING-MODE-DESIGN.md) that an extraction has no way to supply,
# so a model emitting "interview" used to validate and persist an item
# that every interview code path would then find malformed (#269).
ExtractableKind = Literal["achievement", "skill", "role", "testimonial"]


class ProposedItem(BaseModel):
    """One achievement, skill, role or testimonial proposed by the model."""

    kind: ExtractableKind
    name: str = Field(min_length=1)
    detail: str = ""
    classification: ClaimClassification
    confidence: float = Field(ge=0.0, le=1.0)
    quotes: list[str] = Field(min_length=1)
    # Roles only, and optional even there: a résumé that states a position
    # without dates should still yield a role with the dates visibly
    # absent rather than invented. 'ended' is None for a current role —
    # which is why it cannot be inferred from 'started' alone.
    company: str = ""
    title: str = ""
    started: str = ""  # 'YYYY' or 'YYYY-MM', normalized by the extractor
    ended: str = ""

    @property
    def item_kind(self) -> ProfileItemKind:
        return ProfileItemKind(self.kind)


class ExtractionProposal(BaseModel):
    """The full model output: a list of proposed items, nothing else."""

    items: list[ProposedItem]


class ProposedRequirement(BaseModel):
    """One requirement proposed from a job description."""

    name: str = Field(min_length=1)
    detail: str = ""
    kind: RequirementKind
    quotes: list[str] = Field(min_length=1)


class RequirementsProposal(BaseModel):
    requirements: list[ProposedRequirement]


class ProposedAssessment(BaseModel):
    """One fit verdict proposed by the assessment model."""

    requirement_id: str
    verdict: FitVerdict
    rationale: str = ""
    evidence_item_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class AssessmentProposal(BaseModel):
    assessments: list[ProposedAssessment]
