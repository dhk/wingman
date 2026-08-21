"""Good and bad examples (#438): the judgment that usually evaporates.

Wingman produces documents constantly — briefings, POVs, cover letters,
outreach drafts — and some come out well and some badly. That judgment
lives for the length of one conversation and then is gone, so the same
failure recurs and the good version cannot be pointed at later.

An example is a document plus three facts about it: whether it was good
or bad, what KIND of document it is an example of, and WHY. The reason is
required on both verdicts, not just the bad ones. A bare 'bad' teaches
nothing six weeks later and cannot be reviewed by anyone else; "bad — it
over-claims seniority" is the part that is actually reusable.

Deliberately NOT here: anything that feeds these back into generation as
style references. That changes what wingman produces and needs evaluating
against the evidence rules in AGENTS.md, and it needs a corpus to exist
before it can work at all. This module is that corpus.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class ExampleVerdict(StrEnum):
    """The user's own call on a document. Never inferred."""

    GOOD = "good"
    BAD = "bad"


class ExampleAuthor(StrEnum):
    """Who wrote the thing being judged.

    Worth keeping separate from the verdict: "wingman wrote this and it
    was bad" and "someone else wrote this and it was good" are different
    lessons, and a corpus that cannot tell them apart cannot teach either.
    """

    WINGMAN = "wingman"
    USER = "user"


#: A kind is free text rather than a fixed enum, normalised to lowercase.
#: Fixed would catch typos, but it would also mean a save can fail because
#: the taxonomy has not caught up with the work — and capture that refuses
#: is capture that does not happen. The vocabulary accretes instead, and
#: 'examples' action='kinds' makes drift visible rather than impossible.
MAX_KIND_LENGTH = 60


class Example(BaseModel):
    """One judged document, kept verbatim."""

    example_id: str = Field(default_factory=lambda: str(uuid4()))
    verdict: ExampleVerdict
    kind: str = Field(min_length=1, max_length=MAX_KIND_LENGTH)
    #: Why it is good or bad. Required on both — this is the reusable part.
    reason: str = Field(min_length=1)
    text: str = Field(min_length=1)
    author: ExampleAuthor
    #: Free-text note on where it came from. Wingman has no notion of a
    #: conversation id, so provenance is what the caller can say plainly.
    source: str = ""
    saved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("kind")
    @classmethod
    def _normalise_kind(cls, value: str) -> str:
        """One vocabulary, not one per capitalisation."""
        return " ".join(value.strip().lower().split())

    @field_validator("reason", "text")
    @classmethod
    def _require_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped
