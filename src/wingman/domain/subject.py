"""Subjects of a relationship beyond people (RFC-085, #647).

RFC-037 gave each watched person a log and an objective. A search is also
about organizations, specific roles, the user's own projects and the things
they write or teach — and one event often concerns several of those at once
("Trent got me a hiring-manager interview at Sage and may introduce my
practice's first customer" is about a person, a company and an initiative).

A `SubjectRef` names any of them by kind and id. People keep their RFC-037
tables untouched; companies are named by `company_key`, the identity every
company table already uses; opportunities by `opportunity_id`, with their
own journal (RFC-076) as their timeline. Initiatives and content pieces are
the only new records, because nothing in the workspace held them before.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

from wingman.domain.relationship import EvidenceTier


class SubjectKind(StrEnum):
    PERSON = "person"
    COMPANY = "company"
    OPPORTUNITY = "opportunity"
    INITIATIVE = "initiative"
    CONTENT = "content"


# The kinds that are records of their own in the `subjects` table.
RECORD_KINDS = (SubjectKind.INITIATIVE, SubjectKind.CONTENT)


class SubjectRef(BaseModel):
    """A pointer to one subject: its kind, its stable id, and the name it
    was shown under when the pointer was written."""

    kind: SubjectKind
    subject_id: str
    label: str

    def key(self) -> tuple[str, str]:
        return (self.kind.value, self.subject_id)


class Subject(BaseModel):
    """An initiative (a project the user runs: a practice, a class) or a
    content piece (a post, talk or class the user writes or gives)."""

    subject_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: SubjectKind
    name: str
    description: str = ""
    url: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def name_key(self) -> str:
        return " ".join(self.name.lower().split())


class SubjectLogEntry(BaseModel):
    """What happened, logged against a company, initiative or content piece.

    The same contract as `RelationshipLogEntry` (RFC-037, RFC-073): the note
    is stored verbatim in its own inbox source file, deterministic, with an
    evidence tier. People keep their own table; opportunities log into their
    journal (RFC-076).
    """

    entry_id: str = Field(default_factory=lambda: str(uuid4()))
    subject: SubjectRef
    source_record_id: str
    note: str
    evidence_tier: EvidenceTier = EvidenceTier.OBSERVED
    source_url: str = ""
    happened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SubjectObjective(BaseModel):
    """The goal / thesis / next-move triple (RFC-037) for a non-person subject.

    One per subject, replaced in place like a person's objective.
    """

    objective_id: str = Field(default_factory=lambda: str(uuid4()))
    subject: SubjectRef
    goal: str
    thesis: str
    next_move: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
