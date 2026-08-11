"""The commentary corpus (RFC-058, issue #339): the assistant's reading OF
the user's material, stored where it can never be mistaken for their words.

Every other capture surface in this workspace is evidence-shaped —
`interview_react` stores the user's words, `qa_capture` their answers,
`corpus add` their writing — so a synthesis the assistant offered
("your three con nominations are one claim, not three") has nowhere to
live. Filing it through any of them would enter the MODEL's words as the
USER's evidence, which is exactly what evidence-before-assertion exists
to prevent: every downstream POV card, outreach brief and fit assessment
cites profile items and corpus documents as things the person said.

So an entry here carries its authorship structurally, not as a
disclaimer inside prose: which model wrote it, under which prompt
version ("none" when a human or an unprompted conversation wrote it),
and when. The references are the other direction of the same discipline
— an entry points at the captures it was drawn from, so a reader can
check the reading against the material instead of taking it on trust.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

# Printed above every rendered entry, everywhere. The store's isolation is
# what actually keeps commentary out of the evidence paths; this is what
# keeps a human (or a model reading a tool result) from misfiling it by hand.
COMMENTARY_BANNER = "COMMENTARY — the assistant's reading, not your own words. Never evidence."

# What `prompt_version` says when nothing generated this from a versioned
# prompt: a conversation, or a human typing it in.
NO_PROMPT_VERSION = "none"

# What `author_model` says when the caller did not name itself. Still not
# the user — an unnamed model is a model.
UNNAMED_MODEL = "unnamed model"


class CommentaryReference(BaseModel):
    """One piece of the user's material a reading was drawn from.

    `label` is resolved and frozen at save time so a listing stays readable
    even after the material is deleted; `ref_id` is the live id a reader
    follows to check the reading against the material itself.
    """

    ref_id: str
    kind: str  # profile-item | corpus-document | external-document | answer
    label: str


class CommentaryEntry(BaseModel):
    """One reading, with the authorship that keeps it from reading as a claim."""

    entry_id: str = Field(default_factory=lambda: str(uuid4()))
    text: str = Field(min_length=1)
    topic: str = ""
    author_model: str = Field(min_length=1)
    prompt_version: str = Field(default=NO_PROMPT_VERSION, min_length=1)
    drawn_from: list[CommentaryReference] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def attribution(self) -> str:
        """One line naming who wrote this, under what, and when."""
        return (
            f"{self.author_model} · prompt {self.prompt_version} · "
            f"{self.created_at.date().isoformat()}"
        )
