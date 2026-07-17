"""Opportunity entities: a role under consideration and its evidence-mapped assessment."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

from wingman.domain.profile import EvidenceSpan


class RequirementKind(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"


class FitVerdict(StrEnum):
    MET = "met"
    PARTIAL = "partial"
    GAP = "gap"
    UNKNOWN = "unknown"


class OpportunityStatus(StrEnum):
    ASSESSED = "assessed"


class Requirement(BaseModel):
    """One requirement extracted from a job description, cited to its source."""

    requirement_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    detail: str = ""
    kind: RequirementKind
    evidence: list[EvidenceSpan] = Field(min_length=1)


class RequirementAssessment(BaseModel):
    """The fit verdict for one requirement, citing profile items as evidence.

    A met/partial verdict is only valid with at least one resolvable profile
    item ID — deterministic validation downgrades unsupported verdicts to
    unknown rather than presenting polished fiction.
    """

    requirement_id: str
    verdict: FitVerdict
    rationale: str
    evidence_item_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class Opportunity(BaseModel):
    """A role under consideration: requirements, fit assessment, status, next action."""

    opportunity_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    source_record_id: str
    status: OpportunityStatus = OpportunityStatus.ASSESSED
    next_action: str
    requirements: list[Requirement]
    assessments: list[RequirementAssessment]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
