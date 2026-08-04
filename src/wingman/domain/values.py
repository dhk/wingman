"""Value-dimension inference: v2 of issue #240 (docs/RFC.md RFC-051).

Turns a person's own accumulated Values/Mission-alignment interview
captures (`domain.profile.ProfileItem`, `intensity`/`company_reason` per
v1, RFC-050) into a small, named set of per-person value axes. Axes are
derived from the person's own content, never a fixed preset (the owner's
explicit constraint) — `ValueAxis.name`/`description` are model-proposed
and validated only for having real supporting evidence, never checked
against a taxonomy.

`ValueAxis.score` is the STABLE CONTRACT v3 (a later, separate PR — the
radar-chart visualization) is expected to consume: a float in [-1.0, 1.0],
computed deterministically (never by the model — AGENTS.md's "no model for
arithmetic") from the signed intensity of the axis's own supporting items,
never asked of or trusted from the model directly. `label` is a short,
deterministically-bucketed human-readable summary of the same score, for
a caller that wants prose instead of (or alongside) the number.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class ValueAxisEvidence(BaseModel):
    """One captured interview item that fed a value axis — the
    traceability contract (AGENTS.md, RFC-005): every axis must point
    back to which specific captures informed it, never a black-box
    number. `signed_weight` is this item's own deterministic contribution
    to the axis `score` (see `ValueAxis.score`), in [-1.0, 1.0]: the
    item's captured `intensity` as a magnitude, signed by whether its
    subtype is the `_pro` or `_con` side of the nomination."""

    item_id: str
    subtype: str
    target: str
    quote: str
    intensity: str | None = None
    signed_weight: float = Field(ge=-1.0, le=1.0)


class ValueAxis(BaseModel):
    """One inferred value dimension.

    `score` is the stable v3 contract: -1.0 (strongly repelled/against)
    .. +1.0 (strongly drawn to/aligned), 0.0 mixed or neutral — the
    deterministic mean of `evidence`'s `signed_weight` values, never
    model-computed. `label` is a short deterministic summary of the same
    score band (see `application.values._label_for_score`), not
    model-proposed prose, so it can never disagree with `score`.
    """

    name: str = Field(min_length=1)
    description: str = ""
    score: float = Field(ge=-1.0, le=1.0)
    label: str
    evidence: list[ValueAxisEvidence] = Field(min_length=1)


class ValueProfile(BaseModel):
    """The current inferred value-dimension profile for one person (or
    coaching Persona); rebuilt on demand, same "rebuilt, not versioned"
    lifecycle as `domain.pov.PovCard` — a fresh call replaces the prior
    stored profile rather than keeping generations side by side.

    `source_item_ids` records exactly which ProfileItem ids fed this
    computation, so staleness can be answered deterministically at read
    time (`application.values.new_captures_since`) by diffing against
    the currently-eligible item set — no model call needed to detect it,
    only to recompute.
    """

    profile_id: str = Field(default_factory=lambda: str(uuid4()))
    subject_id: str
    subject_name: str
    axes: list[ValueAxis] = Field(default_factory=list)
    items_used: int
    source_item_ids: list[str] = Field(default_factory=list)
    provider: str
    model: str
    prompt_version: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProposedValueAxis(BaseModel):
    """A model-proposed axis, before deterministic validation and scoring."""

    name: str = ""
    description: str = ""
    item_ids: list[str] = Field(default_factory=list)


class ValueAxisProposal(BaseModel):
    """The model's full proposal for a value profile."""

    axes: list[ProposedValueAxis] = Field(default_factory=list)
