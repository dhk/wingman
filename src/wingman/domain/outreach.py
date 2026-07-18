"""Outreach briefs: draft talking points connecting a person's POV to your writing.

A brief is decision support for reaching out — never the outreach itself
(RFC-006: Wingman sends nothing). Every talking point is admissible only if
it cites one of the person's validated stances exactly and quotes the user's
own corpus verbatim: the model proposes, deterministic validation disposes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from enum import StrEnum

from pydantic import BaseModel, Field

from wingman.domain.pov import StanceDimension


class OutreachPurpose(StrEnum):
    """Why the user is reaching out — shapes the draft, never sent anywhere."""

    INTRODUCTION = "introduction"  # first contact, no presumed familiarity
    RECONNECTION = "reconnection"  # picking a real relationship back up
    JOB = "job"  # interest in working at their company
    ADVICE = "advice"  # asking for their perspective on shared ground


class TalkingPoint(BaseModel):
    """One bridge between something they argue and something you wrote."""

    point: str = Field(min_length=1)
    their_stance: str = Field(min_length=1)
    your_quote: str = Field(min_length=1)
    corpus_doc_id: str
    corpus_doc_title: str
    # Inherited deterministically from the cited card stance — the model
    # never labels the talking point itself.
    dimension: StanceDimension | None = None


class OutreachBrief(BaseModel):
    """The current draft brief for one person; rebuilt on demand."""

    brief_id: str = Field(default_factory=lambda: str(uuid4()))
    person_id: str
    person_name: str
    talking_points: list[TalkingPoint] = Field(default_factory=list)
    # Bullets of raw intro material — the user composes the actual message in
    # their own voice. Deliberately not a ready-to-send draft.
    intro_points: list[str] = Field(default_factory=list)
    purpose: OutreachPurpose = OutreachPurpose.INTRODUCTION
    alignment: float | None = None
    corpus_documents_used: int
    pov_generated_at: datetime
    provider: str
    model: str
    prompt_version: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProposedTalkingPoint(BaseModel):
    """A model-proposed talking point, before deterministic validation."""

    point: str
    their_stance: str
    corpus_doc_id: str
    your_quote: str


class OutreachProposal(BaseModel):
    """The model's full proposal for a brief."""

    talking_points: list[ProposedTalkingPoint] = Field(default_factory=list)
    intro_points: list[str] = Field(default_factory=list)
