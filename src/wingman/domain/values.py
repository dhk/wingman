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

The SIGN of an item's contribution is `AxisDirection` — the model's
per-item judgment of whether that capture supports or opposes the axis
AS NAMED (RFC-056) — never the `_pro`/`_con` suffix of its subtype. A
condemnation of somebody who violated a value is evidence the person
HOLDS that value; reading the sign off the subtype inverted exactly
those axes (issue #340).

RFC-057 (issue #343) augments that rather than replacing it: a capture
may now also carry the person's own answer to "what does that tell us you
value?", which is positive by construction and so makes the direction
judgment a reading of an explicit statement rather than an inference from
a verdict. Captures predating the question have none, so `direction` is
still what the sign comes from in every case.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class AxisDirection(StrEnum):
    """Which way one cited capture cuts, relative to the axis AS NAMED.

    SUPPORTS: this capture is evidence the person holds/is drawn to the
    named value — whether they admired somebody who embodied it (`_pro`)
    or were disgusted by somebody who trampled it (`_con`).
    OPPOSES: this capture is evidence the person rejects the named
    dimension — which a `_pro` nomination can equally well be, when the
    model names an axis the person's admiration argues against.

    Classifying this is semantic judgment, so the model does it; turning
    it into a number is arithmetic, so code does that (AGENTS.md).
    """

    SUPPORTS = "supports"
    OPPOSES = "opposes"


class ValueAxisEvidence(BaseModel):
    """One captured interview item that fed a value axis — the
    traceability contract (AGENTS.md, RFC-005): every axis must point
    back to which specific captures informed it, never a black-box
    number. `signed_weight` is this item's own deterministic contribution
    to the axis `score` (see `ValueAxis.score`), in [-1.0, 1.0]: the
    item's captured `intensity` as a magnitude, signed by `direction` —
    the model's judgment of whether this capture supports or opposes the
    axis as named.

    `direction` is `None` only for a profile stored before RFC-056 (issue
    #340) added it. Such a profile's scores were computed under the old
    sign-from-subtype rule and are not trustworthy — every writer since
    sets it, and `application.values.render_value_profile` says so on
    read rather than presenting the stale numbers as current.

    `value_statement` (RFC-057, issue #343) is what the person said this
    nomination tells them they VALUE, in their own words — empty for a
    capture made before the interview asked. It is carried here for the
    same reason `quote` is: an axis inferred partly from a positive value
    statement should show the reader that statement, not only the verdict
    about the nominee that occasioned it.
    """

    item_id: str
    subtype: str
    target: str
    quote: str
    intensity: str | None = None
    direction: AxisDirection | None = None
    value_statement: str = ""
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


class ProposedAxisCitation(BaseModel):
    """One cited capture in a model proposal: which item, and which way it
    cuts relative to the axis the model just named.

    `direction` is typed loosely here, not as `AxisDirection`, deliberately:
    the model is untrusted output, and one unparseable direction must
    cost that one citation, not blow up the whole proposal. It is
    resolved (and anything unrecognized dropped) in
    `application.values._validate_proposal`, the same place an unknown
    `item_id` is dropped.

    `Any` rather than `str` because a `str` annotation makes pydantic
    reject the whole response before that dropping can happen: a model
    emitting `"direction": null` — or a number, or an object — fails
    validation on this field and takes every other axis in the proposal
    down with it, which is the opposite of what the paragraph above
    promises. Anything not a recognized string is dropped by
    `_direction`.
    """

    item_id: str
    direction: Any = ""


class ProposedValueAxis(BaseModel):
    """A model-proposed axis, before deterministic validation and scoring."""

    name: str = ""
    description: str = ""
    items: list[ProposedAxisCitation] = Field(default_factory=list)


class ValueAxisProposal(BaseModel):
    """The model's full proposal for a value profile."""

    axes: list[ProposedValueAxis] = Field(default_factory=list)
