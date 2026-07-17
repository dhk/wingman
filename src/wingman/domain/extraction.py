"""The schemas model outputs must satisfy before deterministic validation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from wingman.domain.opportunity import FitVerdict, RequirementKind
from wingman.domain.profile import ProfileItemKind
from wingman.domain.provenance import ClaimClassification


class ProposedItem(BaseModel):
    """One achievement or skill proposed by the extraction model."""

    kind: ProfileItemKind
    name: str = Field(min_length=1)
    detail: str = ""
    classification: ClaimClassification
    confidence: float = Field(ge=0.0, le=1.0)
    quotes: list[str] = Field(min_length=1)


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
