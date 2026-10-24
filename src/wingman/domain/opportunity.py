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
    """Where a pursuit stands (RFC-076). ASSESSED is the stage every
    opportunity starts at; every other stage is set only by an explicit
    human act through the opportunity journal, never inferred from age,
    pack files, or a model judgment.

    REJECTED and WITHDRAWN are kept apart on purpose: "they said no" and
    "I said no" call for different next moves.
    """

    ASSESSED = "assessed"
    SHORTLISTED = "shortlisted"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class OpportunityEvent(BaseModel):
    """One append-only entry in an opportunity's journal (RFC-076, #496).

    A stage change, a working note, or both. The latest event carrying a
    stage is the opportunity's current stage; nothing is ever overwritten,
    so "rejected, then revived after a recruiter called" keeps both events.
    A note is the user's own working words ("draft pack, apply Monday") —
    a decision record, never career evidence: it is not a source record
    and never reaches the profile, an assessment, or a pack.
    """

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    opportunity_id: str
    stage: OpportunityStatus | None = None
    note: str = ""
    happened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    """A role under consideration: requirements, fit assessment, status, next action.

    `status` is the stage recorded when the assessment was saved (always
    ASSESSED); the current stage lives in the opportunity journal
    (RFC-076), so re-assessing a role never resets where its pursuit stands.
    """

    opportunity_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    source_record_id: str
    status: OpportunityStatus = OpportunityStatus.ASSESSED
    next_action: str
    requirements: list[Requirement]
    assessments: list[RequirementAssessment]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def count_verdicts(assessments: list[RequirementAssessment]) -> dict[FitVerdict, int]:
    """How many requirement assessments landed at each verdict.

    Pulled out so the fit brief and the opportunity list (#312) count the
    same way instead of two implementations quietly drifting apart.
    """
    return {verdict: sum(1 for a in assessments if a.verdict is verdict) for verdict in FitVerdict}
