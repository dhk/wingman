"""The schema a model's extraction output must satisfy before validation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from wingman.domain.profile import ProfileItemKind
from wingman.domain.provenance import ClaimClassification


class ProposedItem(BaseModel):
    """One achievement or skill proposed by the extraction model."""

    kind: ProfileItemKind
    name: str = Field(min_length=1)
    detail: str = ""
    classification: ClaimClassification
    confidence: float = Field(ge=0.0, le=1.0)
    quotes: list[str] = Field(min_length=1)


class ExtractionProposal(BaseModel):
    """The full model output: a list of proposed items, nothing else."""

    items: list[ProposedItem]
