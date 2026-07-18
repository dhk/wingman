"""POV cards: evidence-backed summaries of what a watched person thinks.

A stance is admissible only with a verbatim quote from one of the person's
stored documents (RFC-005 discipline applied to other people's writing):
the model proposes, deterministic validation disposes, and every stored
stance carries its source record.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class StanceDimension(StrEnum):
    """What kind of concordance a stance offers for outreach.

    A fixed taxonomy so the model must choose, never invent: values (what
    they hold as right or important), attitude (outlook and disposition —
    optimism, skepticism, contrarianism), technical (methods, architecture,
    craft), strategy (where markets, products, or organizations should go).
    """

    VALUES = "values"
    ATTITUDE = "attitude"
    TECHNICAL = "technical"
    STRATEGY = "strategy"


class Stance(BaseModel):
    """One position the person holds, backed by their own words."""

    statement: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    doc_id: str
    doc_title: str
    source_record_id: str
    organization: str | None = None
    # None = uncategorized (pre-taxonomy cards, or the model proposed an
    # invalid label and deterministic validation refused to guess).
    dimension: StanceDimension | None = None


class PovCard(BaseModel):
    """The current point-of-view summary for one person; rebuilt on demand."""

    card_id: str = Field(default_factory=lambda: str(uuid4()))
    person_id: str
    person_name: str
    stances: list[Stance] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    documents_used: int
    provider: str
    model: str
    prompt_version: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProposedStance(BaseModel):
    """A model-proposed stance, before deterministic validation."""

    statement: str
    quote: str
    doc_id: str
    dimension: str = ""


class PovProposal(BaseModel):
    """The model's full proposal for a card."""

    stances: list[ProposedStance] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
