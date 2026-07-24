"""Relationship objectives (RFC-037): the thesis per person, in their own words.

Every watched person can carry a relationship objective: why the user is
investing (goal), what they believe about the path (thesis), and the next
intended move — the user's own confirmed words, persisted, editable, seeded
and revised by an interview conversation (docstring-protocol convention,
RFC-025/030/031/035/036). One objective per person: saving always replaces
the current triple in place (the POV-card pattern), because the objective is
a living judgment, not a lineage of superseded claims.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class RelationshipObjective(BaseModel):
    """The goal/thesis/next-move triple for one relationship.

    goal: why the user is investing in this relationship.
    thesis: what they believe about the path from here.
    next_move: the next intended action, concrete enough to act on.
    All three are the user's own confirmed wording — never a model's
    free-floating judgment (RFC-037's evidence-gate ethos applies here too).
    """

    objective_id: str = Field(default_factory=lambda: str(uuid4()))
    person_id: str
    goal: str
    thesis: str
    next_move: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RelationshipLogEntry(BaseModel):
    """One recorded interaction with a person — what actually happened.

    Deterministic and person-attributed, the qa_capture way (RFC-036): the
    user's own words become the evidence quote in a source file, zero model
    calls between what was typed and what is stored. The raw material every
    future brief and thesis revision cites.
    """

    entry_id: str = Field(default_factory=lambda: str(uuid4()))
    person_id: str
    source_record_id: str
    note: str
    happened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
