"""Application answer bank entities (RFC-030).

One record per refined answer: the question as asked, the answer the user
settled on after iterating, and the application context it was refined
for. Reuse across applications is the point — records persist and are
searched when a similar question shows up for a different role.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class AnswerRecord(BaseModel):
    answer_id: str = Field(default_factory=lambda: str(uuid4()))
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    company: str = ""
    role_title: str = ""
    asked_on: str = ""  # the application/interview date as the user stated it
    # How the answer reached the bank, when it was not refined in
    # conversation (#287) — e.g. domain.provenance.FORM_ARRIVAL. Empty for
    # every entry saved before this existed and for every ordinary
    # conversational save, which is the same thing said twice: silence
    # means "the assistant and the user settled this together", and that
    # is what the bank has always held.
    source: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def context(self) -> str:
        parts = [
            part for part in (self.company, self.role_title, self.asked_on, self.source) if part
        ]
        return ", ".join(parts) if parts else "(no context)"
