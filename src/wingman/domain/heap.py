"""The heap (#113): a capture-first inbox for leads that arrive faster than
they can be sorted — a job posting, a LinkedIn profile, a company site, all
dropped at once with zero processing. Every item carries a heat rating
(hot/warm/cold) that orders everything downstream; heat is the only
judgment captured at drop time, never inferred.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class HeapHeat(StrEnum):
    """How alive a lead is right now — the user's own call, never inferred."""

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class HeapItem(BaseModel):
    """One captured lead: a raw reference (URL, or any short text), verbatim."""

    item_id: str = Field(default_factory=lambda: str(uuid4()))
    item: str = Field(min_length=1)
    heat: HeapHeat = HeapHeat.WARM
    note: str = ""
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
