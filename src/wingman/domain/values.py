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

RFC-066 (issue #356) adds a second READING of the same captures rather
than a second pipeline: `ValueView` names it, `ValueProfile.view` carries
it, and everything below — the score, the sign, the buckets, the
traceability — is identical for both. Only the prompt's naming
instruction, the eligible-capture set, and the wording of what the
artefact discloses about itself differ.

**Why the contract versions live here** (issue #355, RFC-063). `SCORING_CONTRACT_VERSION`
and `RADAR_CONTRACT_VERSION` name behaviour implemented elsewhere —
`application.values` scores, `reporting.radar` draws. They are declared in
the domain anyway because a contract version is part of the artefact's
data contract rather than of the code that satisfies it, and three layers
have to agree on the same string: application stamps it onto the stored
`ValueProfile`, reporting stamps it into the exported SVG, and
`application.freshness` compares both against what is current. Domain is
the only layer all three may import (RFC-001), so putting the constants
beside the models they describe is what keeps the comparison from becoming
a sideways import.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

#: The version of the DETERMINISTIC half of this artefact's contract: the
#: rule that turns captures into `ValueAxis.score`/`label` — magnitude from
#: `intensity`, sign from `direction`, the mean over an axis's evidence, and
#: the score->prose buckets. Bump it whenever OUTPUT SEMANTICS change: the
#: same captures would now produce a different number, a different sign, or
#: a different label. Do not bump it for a refactor, a comment, or a
#: performance change that leaves every output identical.
#:
#: `prompt_version` already versions the MODEL half (which axes get named,
#: how direction is judged). This is the half it never covered, and #340 was
#: a change to exactly this half: the sign flipped for every axis evidenced
#: by a condemnation, so every profile and every exported chart built before
#: the fix asserted the opposite of the truth with nothing on it to say so.
#: A version stamped on the artefact makes that detectable after the fact
#: instead of relying on somebody noticing the numbers look wrong.
#: AGENTS.md already requires it of anything that scores ("Scores must
#: expose ... scoring-rule version"); this is that requirement, honoured.
#:
#: `tests/contract/test_values_contract_version.py` fails if the behaviour
#: this names changes without this string moving — the rule is enforced, not
#: remembered.
SCORING_CONTRACT_VERSION = "values-scoring-1"

#: The version of the chart's geometry contract: how a signed score becomes
#: a radius (`reporting.radar._radius_fraction`), and therefore what shape a
#: reader is being asked to believe. Kept SEPARATE from the scoring version
#: on purpose: remapping the geometry makes an exported chart wrong without
#: making the stored `ValueProfile` wrong, and one version covering both
#: would mark every stored profile stale for a change that never touched a
#: number.
RADAR_CONTRACT_VERSION = "values-radar-1"


class ValueView(StrEnum):
    """Which READING of the same captures a profile is (issue #356, RFC-066).

    The captures do not change; the register the axes are named in does.

    CHARACTER — what this person cares about as a person: "compassion for
    the marginalized", "accountability over power". This is what RFC-051
    always built, and it is the point of the Values interview.
    WORK — how this person wants to WORK: what they want authority over,
    what conditions they need, what they will and will not ship. The same
    capture that reads as *honesty* on the character view reads as
    *verification and auditability as a precondition for shipping* here.

    Two views rather than two axis sets: the scoring rule, the per-item
    `direction` contract (RFC-056) and the storage lifecycle are identical,
    and only the model's naming instruction differs. A parallel pipeline
    would have meant two implementations of arithmetic that must never
    disagree.

    The character view exists so nothing that already reads a `ValueProfile`
    has to know views exist: it is the default everywhere, including for a
    profile stored before this enum did.
    """

    CHARACTER = "character"
    WORK = "work"


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

    `view` (issue #356) says which READING these axes are — the character
    one RFC-051 always built, or the work one a fit assessment can cite.
    It is a discriminator on the same subject rather than a second key, so
    a workspace holds at most one profile per (subject, view) and the two
    views can never be confused for each other by a reader that forgot to
    ask. It defaults to CHARACTER, which is what every profile stored
    before the field existed actually is.

    `scoring_version` is the other half of that question (#355): inputs
    can sit still while the CODE that shaped them moves. It records the
    `SCORING_CONTRACT_VERSION` in force when this profile's numbers were
    computed, so a profile scored under a rule the codebase has since
    replaced can be recognized as superseded without re-deriving it.
    Empty means the profile was stored before the field existed — which
    includes every profile scored under the inverted sign rule #340
    fixed, so empty is treated as "not current", never as "fine".
    """

    profile_id: str = Field(default_factory=lambda: str(uuid4()))
    subject_id: str
    subject_name: str
    view: ValueView = ValueView.CHARACTER
    axes: list[ValueAxis] = Field(default_factory=list)
    items_used: int
    source_item_ids: list[str] = Field(default_factory=list)
    provider: str
    model: str
    prompt_version: str
    scoring_version: str = ""
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
