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
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class EvidenceTier(StrEnum):
    """How a relationship_log entry's note relates to what actually
    happened (RFC-073).

    OBSERVED: the note is the user's own words, or a direct quote from a
    real source (an email, a genuine transcript) — RFC-037's original
    "zero model calls" contract, named rather than merely implied.
    ENDORSED: the note is a model-drafted synthesis of a source (an
    AI-generated meeting-notes summary, a gestalt read of a call) that the
    person has reviewed and confirmed or corrected before it was stored.

    There is no third stored value for an unconfirmed draft — "inferred"
    is protocol vocabulary describing that pre-confirmation state, never
    itself written here. By the time a call reaches log_interaction, the
    note is either a real source's own words or a synthesis the person has
    already promoted by confirming it.
    """

    OBSERVED = "observed"
    ENDORSED = "endorsed"


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
    note becomes the evidence quote in a source file, zero model calls
    between what was typed and what is stored, by default. The raw
    material every future brief and thesis revision cites.

    evidence_tier (RFC-073) names what kind of evidence the note is —
    OBSERVED (the default: the user's own words, or a direct source quote)
    or ENDORSED (a model-drafted synthesis the person reviewed and
    confirmed). source_url is an optional pointer to the real external
    document a synthesis was drawn from (a meeting-notes doc, an email
    thread) — distinct from source_record_id, which always points at this
    entry's own generated inbox note.
    """

    entry_id: str = Field(default_factory=lambda: str(uuid4()))
    person_id: str
    source_record_id: str
    note: str
    evidence_tier: EvidenceTier = EvidenceTier.OBSERVED
    source_url: str = ""
    happened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
