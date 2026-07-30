"""Persona: the subject of the coach's own tools, run on their behalf
(docs/COACHING-MODE-DESIGN.md) — deliberately distinct from Person
(domain/person.py's watchlist of people the coach follows). A Person's
writing is fetched for the coach's own benefit; a Persona is the opposite
relationship — someone the coach runs wingman's analyses FOR.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class Persona(BaseModel):
    """Someone the coach is coaching. Deliberately minimal for v1 — a name
    plus optional notes, nothing more."""

    persona_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def name_key(self) -> str:
        """Case/whitespace-insensitive key for find-or-create by name."""
        return " ".join(self.name.lower().split())
