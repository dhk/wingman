"""Value-dimension inference: v2 of issue #240 (docs/RFC.md RFC-051).

v1 (#241, merged) captured `intensity` (mild/moderate/strong) and
`company_reason` directly on Values/Mission-alignment `ProfileItem`s
(`application/interview.py`). This module is the synthesis step v1's own
docstring named as "not built here": read a person's (or coaching
Persona's) own accumulated captures and infer a small, named set of value
dimensions — axes derived from THEIR content, never a fixed preset (the
owner's explicit constraint). v3 (a later, separate PR) renders these axes
as a radar/spider chart; nothing here draws anything.

**Model use.** One `synthesize_balanced` call proposes axis names,
descriptions, which captured item_ids evidence each one, and — per cited
item — whether that item SUPPORTS or OPPOSES the axis as named. That is
the "determine you cared about ~environmental stewardship~ from these
specific nominations" kind of judgment worth a model. What the model
proposes is then deterministically validated (an axis survives only if it
cites at least one item we actually supplied, with a direction we
recognize — the same "model proposes, code disposes" discipline
`application/pov.py` applies to quotes) and SCORED entirely in code —
never by the model, per AGENTS.md's "do not use a model for arithmetic" —
magnitude from each cited item's own captured `intensity`, sign from that
item's direction.

**Why direction, not the subtype (issue #340, RFC-056).** The sign used to
come from whether the item's subtype ended in `_pro` or `_con`. That is a
syntactic tell, not a semantic one: the model names an axis as a VALUE
("Honesty and the right to informed choice"), and a `_con` nomination is a
condemnation of somebody who VIOLATED that value — evidence the person
HOLDS it. Reading the sign off the suffix therefore reported the owner as
"strongly repelled by" honesty and accountability, and cancelled a strong
`_pro` against a strong `_con` (Francis vs. Ratzinger on moral courage) to
exactly 0.00 "mixed / ambivalent" when both cut the same way. Inverting
the old rule ("cons always support") would only relocate the bug — it
breaks the moment the model names an axis as a disvalue — so the
direction is asked for per item instead.

**The value statement (issue #343, RFC-057).** A capture may now carry
the person's own answer to "what does that tell us you value?" — a
POSITIVE statement, by construction, alongside a `why` that for half the
interview is a condemnation. Where one exists it goes into the prompt
next to the quote, so the direction judgment reads an explicit statement
of the value instead of inferring it from a verdict about somebody else.
It augments `direction` rather than replacing it: every capture made
before the question existed has none, and those must keep inferring
exactly as they did.

**The minimum-data floor.** A person with a couple of nominations
shouldn't get a confident multi-axis profile — v3's chart would just be
noise. `MIN_ITEMS`/`MIN_SUBTYPES` (see below) gate inference outright,
`IngestError`, before any model call is made.

**Staleness, both kinds (issue #355, RFC-063).** A `ValueProfile` is a stored,
rebuilt-on-demand artifact — the same lifecycle `domain.pov.PovCard`
already uses (a fresh call replaces the prior one; no versioned history).
It can stop being true two ways, and only one of them used to be
answerable:

  - *New evidence.* `source_item_ids` records exactly which captures fed
    the stored profile, so `new_captures_since` answers it
    deterministically (a set difference against the currently-eligible
    items) without any model call — only a rebuild (`refresh=True`)
    actually re-invokes the model.
  - *Changed code.* The scoring rule below is as much an input to the
    number as the captures are. #340 changed it and every stored profile
    kept displaying its pre-fix numbers as current. So the rule now has a
    version — `domain.values.SCORING_CONTRACT_VERSION`, stamped onto
    every profile this module builds — and `scoring_is_current` answers
    the second question the same way `new_captures_since` answers the
    first: deterministically, from stored data, with no model call.

Both are WARNINGS, never refusals: a superseded profile still renders,
with a line saying what is wrong with it and the one command that fixes
it. That is the precedent RFC-015's stale snapshots and RFC-056's render
warning already set — a refusal would take away the only view of the
evidence at the moment somebody is trying to understand it.

**The work view (issue #356, RFC-066).** The axes above are named as
CHARACTER: "compassion for the marginalized", "accountability over power".
That is what the Values interview asks for and it is real, but nothing
downstream can cite it — `assess_job` scores a posting against
`job-criteria.md`, and a fit brief argues why a role suits somebody;
neither can reach "compassion for the marginalized" without a leap, and
an unaudited leap is what this codebase refuses everywhere else.

So the same captures get a second READING, selected by `ValueView`:

  - The character view is unchanged in every respect.
  - The work view names axes as ways of working — what someone wants
    authority over, the conditions they need, the standard they hold work
    to. Only the prompt's naming instruction differs; the sign contract,
    the arithmetic, the buckets and the storage lifecycle are the same
    code, so the two views can never disagree about how a number is made.
  - The work view is additionally eligible to read the perspective
    reactions (`alignment_of_perspective_*`). Those are reactions to
    IDEAS, not verdicts about people, which makes them the better raw
    material for a work axis — and they are invisible to the character
    view, which asks a question about people.

`WORK_GROUNDING_SUBTYPES` is not a floor (see `work_grounding_note`): a
work profile built only from nominations still renders, and says so.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from wingman.agents.values_analyst import (
    PROMPT_VERSIONS,
    SYSTEM_PROMPT,
    build_prompt,
    parse_value_axis_proposal,
)
from wingman.application.ingest import IngestError
from wingman.application.interview import REACTION_SUBTYPES, SENTIMENT_INTENSITY_SUBTYPES
from wingman.application.pov import CORPUS_PERSON_ID, CORPUS_PERSON_NAME, persona_card_id
from wingman.domain.persona import Persona
from wingman.domain.profile import ItemStatus, ProfileItem, ProfileItemKind, SentimentIntensity
from wingman.domain.values import (
    SCORING_CONTRACT_VERSION,
    AxisDirection,
    ValueAxis,
    ValueAxisEvidence,
    ValueAxisProposal,
    ValueProfile,
    ValueView,
)
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelProvider, ModelRequest

_logger = get_logger("application.values")

# The minimum-data floor (design question #2): below this, inference
# refuses rather than fabricating axes from thin evidence. MIN_ITEMS=6
# mirrors WINGMAN_INTERVIEW_MAX_PER_SUBTYPE's own default (application/
# interview.py) — "a full single subtype's worth" is the bar for having
# enough raw material to say something structured. MIN_SUBTYPES=2 on top
# of that specifically rules out a single lopsided subtype (e.g. six
# values_pro nominations and nothing else) from producing a profile: real
# axis inference needs contrast across at least two of the six eligible
# subtypes (a pro side and a con side, or people alongside organizations)
# to have anything to triangulate.
MIN_ITEMS = 6
MIN_SUBTYPES = 2

# Which captures each view may read (#356).
#
# The character view asks a question about PEOPLE ("name someone you
# admire"), so it reads the nomination subtypes and nothing else —
# unchanged from RFC-051.
#
# The work view reads those AND the perspective reactions. A reaction is a
# response to a claim someone read, which is the only capture in this
# interview that is about ideas rather than about a person; for axes named
# as ways of working that is the more direct evidence, and it carries no
# `intensity` precisely because it has no pro/con polarity to scale (see
# `application.interview`). It is scored at `_DEFAULT_MAGNITUDE` like any
# other intensity-less capture — which is the honest weight for evidence
# whose strength was never asked for, not a penalty.
_ELIGIBLE_SUBTYPES: dict[ValueView, frozenset[str]] = {
    ValueView.CHARACTER: frozenset(SENTIMENT_INTENSITY_SUBTYPES),
    ValueView.WORK: frozenset(SENTIMENT_INTENSITY_SUBTYPES | REACTION_SUBTYPES),
}

#: The bare noun for one view, for a sentence that already carries its own
#: framing ("N value axis(es) survived", "no work profile built yet"). The
#: character view keeps saying "value": that is what every existing message
#: says and what a reader of the character view has always been told.
VIEW_NOUNS: dict[ValueView, str] = {
    ValueView.CHARACTER: "value",
    ValueView.WORK: "work",
}

#: What each view is CALLED where a person reads it. The view is named on
#: every rendering, not only the non-default one: two profiles for the same
#: subject with differently-worded axes, and only one of them labelled, is
#: an invitation to quote the wrong one.
VIEW_TITLES: dict[ValueView, str] = {
    ValueView.CHARACTER: "Value profile (character view)",
    ValueView.WORK: "Work profile (how you want to work)",
}


# The captures that make a work axis a reading of what somebody said about
# WORK rather than a re-reading of a verdict about a person. Used for
# disclosure, never for refusal — see `work_grounding_note`.
WORK_GROUNDING_SUBTYPES = frozenset(REACTION_SUBTYPES)

# Axis count bound (design question #3): a floor of 3 and ceiling of 6,
# the same "3-6, not a fixed preset but not unbounded either" reasoning
# RFC-050 already applied to company_reason's category count. Below 3,
# v3's radar chart has too few points to read as a shape; above 6 it gets
# cluttered and the "small set" the issue asks for stops being small.
# MAX_AXES truncates an over-eager model proposal (same enforcement shape
# as pov.py's MAX_STANCES); MIN_AXES_REQUIRED is checked on the
# VALIDATED result — if evidence only really supports one or two
# distinct axes after invalid citations are stripped out, that's treated
# the same way pov.py treats zero surviving stances: nothing is stored,
# the caller is told to capture more or retry.
MAX_AXES = 6
MIN_AXES_REQUIRED = 3

# Newest-first cap on how many eligible items reach the prompt. The
# natural ceiling is 36 (6 eligible subtypes x the per-subtype capture
# cap of 6), so this is a defensive margin, not a real current bound.
MAX_ITEMS_FOR_INFERENCE = 50

# intensity -> magnitude for the deterministic score (design question
# #4/#6, and RFC-050's own "Revisit if" naming exactly this trigger): a
# three-level ordinal captured at capture time, mapped to an even split
# of [0, 1] for scoring, never replacing the enum as the capture surface.
_INTENSITY_MAGNITUDE: dict[SentimentIntensity, float] = {
    SentimentIntensity.MILD: 1 / 3,
    SentimentIntensity.MODERATE: 2 / 3,
    SentimentIntensity.STRONG: 1.0,
}
# An item captured before intensity was asked for (or where the answer
# was skipped — RFC-050's fields are protocol-, not code-, enforced)
# still carries real evidence value; it is scored as a moderate signal
# rather than silently dropped from the axis's arithmetic or forced to
# the extremes it never claimed.
_DEFAULT_MAGNITUDE = 2 / 3


class RejectedAxis(BaseModel):
    name: str
    reason: str


class ValueProfileReport(BaseModel):
    profile: ValueProfile
    rejected: list[RejectedAxis] = Field(default_factory=list)


def _direction(raw: object) -> AxisDirection | None:
    """The model's per-item direction, resolved — or None if it gave one we
    don't recognize (or none at all). None means the citation is dropped,
    exactly as an unknown item_id is: a missing sign is never defaulted to
    a sign, because a defaulted sign is precisely the bug (#340).

    Takes `object`, not `str`: the field it reads is deliberately untyped
    so a malformed value costs one citation instead of the whole
    proposal, which means a non-string can reach here and must be
    dropped rather than raise.
    """
    if not isinstance(raw, str):
        return None
    try:
        return AxisDirection(raw.strip().lower())
    except ValueError:
        return None


def _target_from_item(item: ProfileItem) -> str:
    """The nominated person/org name, stripped back out of
    interview.py's `_item_name` shape ('{subtype}: {target}[ (persona:id)]')
    — display-only, never re-parsed for anything semantic."""
    name = item.name
    prefix = f"{item.subtype}: "
    name = name.removeprefix(prefix)
    persona_suffix = name.rfind(" (persona:")
    if persona_suffix != -1 and name.endswith(")"):
        name = name[:persona_suffix]
    return name


def _signed_weight(item: ProfileItem, direction: AxisDirection) -> float:
    """This item's contribution to one axis: magnitude from its own
    captured `intensity`, sign from the model's per-item direction for
    THAT axis. The same item can therefore weigh +1.0 on one axis and
    -1.0 on another — which is the point: a capture's meaning is relative
    to the dimension it is being read against, not fixed by its subtype."""
    magnitude = (
        _INTENSITY_MAGNITUDE.get(item.intensity, _DEFAULT_MAGNITUDE)
        if item.intensity is not None
        else _DEFAULT_MAGNITUDE
    )
    return magnitude * (1.0 if direction is AxisDirection.SUPPORTS else -1.0)


def _label_for_score(score: float) -> str:
    """Deterministic prose bucket for `score` — never model-proposed, so
    it can never disagree with the number it describes."""
    if score >= 0.6:
        return "strongly drawn to"
    if score >= 0.2:
        return "leans toward"
    if score > -0.2:
        return "mixed / ambivalent"
    if score > -0.6:
        return "leans away from"
    return "strongly repelled by"


def _newest(items: list[ProfileItem], limit: int) -> list[ProfileItem]:
    return sorted(items, key=lambda item: item.extracted_at, reverse=True)[:limit]


def _items_block(items: list[ProfileItem]) -> str:
    parts = []
    for item in items:
        lines = [
            f"--- item_id: {item.item_id}",
            f"subtype: {item.subtype}",
            f"target: {_target_from_item(item)}",
            f"intensity: {item.intensity.value if item.intensity else 'not given'}",
        ]
        if item.company_reason is not None:
            lines.append(f"company_reason: {item.company_reason.value}")
        lines.append(f"why: {item.detail}")
        # RFC-057: when the person said what this nomination tells them
        # they value, the model reads that alongside the 'why' — a
        # statement that is positive by construction, next to a verdict
        # that may not be. Absent for every capture that predates the
        # question, so the line is conditional rather than "not given":
        # a labelled blank invites the model to fill it in.
        if item.value_statement:
            lines.append(f"what this tells them they value: {item.value_statement}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _eligible_items(
    storage: Storage, persona_id: str | None, view: ValueView = ValueView.CHARACTER
) -> list[ProfileItem]:
    subtypes = _ELIGIBLE_SUBTYPES[view]
    return [
        item
        for item in storage.list_profile_items()
        if item.kind is ProfileItemKind.INTERVIEW
        and item.status is ItemStatus.ACTIVE
        and item.persona_id == persona_id
        and item.subtype in subtypes
    ]


def _validate_proposal(
    proposal: ValueAxisProposal, eligible: dict[str, ProfileItem]
) -> tuple[list[ValueAxis], list[RejectedAxis]]:
    """Model proposes groupings and per-item directions, this disposes:
    every axis must cite at least one item_id we actually supplied WITH a
    direction we recognize, and its score is computed here from those
    items' own captured fields — never trusted from or asked of the model
    (AGENTS.md's "no model for arithmetic")."""
    axes: list[ValueAxis] = []
    rejected: list[RejectedAxis] = []
    for candidate in proposal.axes:
        name = candidate.name.strip()
        if len(axes) >= MAX_AXES:
            rejected.append(
                RejectedAxis(name=name or "(empty)", reason=f"over the {MAX_AXES}-axis limit")
            )
            continue
        if not name:
            rejected.append(RejectedAxis(name="(empty)", reason="empty axis name"))
            continue
        seen: set[str] = set()
        evidence: list[ValueAxisEvidence] = []
        for citation in candidate.items:
            if citation.item_id in seen:
                continue
            item = eligible.get(citation.item_id)
            if item is None:
                continue  # not among the supplied items — silently dropped, not fabricated
            direction = _direction(citation.direction)
            if direction is None:
                # Same discipline as the unknown item_id above: an
                # unusable citation costs that citation. Scoring it
                # anyway would mean inventing the one thing #340 proved
                # we must not infer.
                continue
            # Marked seen only now that both checks have passed. Marking on
            # sight let an unusable citation suppress a LATER valid one for
            # the same item — [no direction, supports] dropped both, and
            # could take the axis down with them for citing nothing usable.
            seen.add(citation.item_id)
            evidence.append(
                ValueAxisEvidence(
                    item_id=item.item_id,
                    subtype=item.subtype or "",
                    target=_target_from_item(item),
                    quote=item.detail,
                    intensity=item.intensity.value if item.intensity else None,
                    direction=direction,
                    value_statement=item.value_statement,
                    signed_weight=_signed_weight(item, direction),
                )
            )
        if not evidence:
            rejected.append(
                RejectedAxis(
                    name=name,
                    reason="cited no item_id, with a usable direction, among the supplied items",
                )
            )
            continue
        score = sum(span.signed_weight for span in evidence) / len(evidence)
        axes.append(
            ValueAxis(
                name=name,
                description=candidate.description.strip(),
                score=score,
                label=_label_for_score(score),
                evidence=evidence,
            )
        )
    return axes, rejected


def _not_enough_evidence(
    subject: str, view: ValueView, eligible: int, subtypes_present: int
) -> IngestError:
    """The refusal below the floor, worded for the view that hit it.

    The character view's advice ("capture both pro and con nominations") is
    wrong advice for the work view, whose best evidence is the perspective
    reactions the character view cannot even see — telling somebody to go
    nominate more people when what the pass needs is reactions to ideas
    sends them to do the wrong work.
    """
    what = (
        "Values/Mission-alignment item(s)"
        if view is ValueView.CHARACTER
        else "Values/Mission-alignment or perspective-reaction item(s)"
    )
    how = (
        "e.g. both values_pro and values_con nominations, or add mission_alignment ones"
        if view is ValueView.CHARACTER
        else "reactions to something you read (alignment_of_perspective_agree/_disagree) are "
        "the most direct evidence for how you want to work"
    )
    return IngestError(
        f"not enough captured evidence to infer {VIEW_NOUNS[view]} dimensions for {subject} yet — "
        f"{eligible} {what} across {subtypes_present} subtype(s) captured; need at least "
        f"{MIN_ITEMS} items spanning at least {MIN_SUBTYPES} subtypes ({how}). Capture more "
        "with 'wingman interview' or interview_react, then try again."
    )


def build_value_profile(
    storage: Storage,
    provider: ModelProvider,
    persona: Persona | None = None,
    view: ValueView = ValueView.CHARACTER,
) -> ValueProfileReport:
    """Build (or rebuild) the value-dimension profile for the user's own
    captures, or (with persona set) a coached Persona's own captures
    instead — the same coaching-mode scoping `application.pov.build_own_pov`
    already uses ("my evidence and their point of view never mix",
    docs/COACHING-MODE-DESIGN.md, extended to this axis).

    `view` (#356) selects the READING, not the pipeline: the same eligible
    captures, the same validation, the same arithmetic. It changes which
    prompt names the axes and, for the work view, widens the eligible set
    to include the perspective reactions. Each view is rebuilt on its own
    call — deliberately not both at once, because one view failing
    validation would then destroy a rebuild of the other, and a refresh
    somebody asked for must not fail on account of a view they did not.

    Raises IngestError before any model call if the minimum-data floor
    (MIN_ITEMS/MIN_SUBTYPES) isn't met, or after the model call if fewer
    than MIN_AXES_REQUIRED axes survive validation — in both cases
    nothing is stored.
    """
    persona_id = persona.persona_id if persona is not None else None
    eligible = _eligible_items(storage, persona_id, view)
    subtypes_present = {item.subtype for item in eligible}
    if len(eligible) < MIN_ITEMS or len(subtypes_present) < MIN_SUBTYPES:
        subject = persona.name if persona is not None else "you"
        raise _not_enough_evidence(subject, view, len(eligible), len(subtypes_present))
    eligible = _newest(eligible, MAX_ITEMS_FOR_INFERENCE)

    prompt = build_prompt(_items_block(eligible), view)
    response = provider.complete(ModelRequest(system=SYSTEM_PROMPT, prompt=prompt))
    proposal = parse_value_axis_proposal(response.text)
    axes, rejected = _validate_proposal(proposal, {item.item_id: item for item in eligible})
    if len(axes) < MIN_AXES_REQUIRED:
        raise IngestError(
            f"only {len(axes)} {VIEW_NOUNS[view]} axis(es) survived validation ({len(rejected)} "
            f"rejected) — need at least {MIN_AXES_REQUIRED} for a meaningful profile. "
            "Nothing was stored; capture more evidence or re-run to retry."
        )

    profile = ValueProfile(
        subject_id=persona_card_id(persona.persona_id) if persona is not None else CORPUS_PERSON_ID,
        subject_name=persona.name if persona is not None else CORPUS_PERSON_NAME,
        view=view,
        axes=axes,
        items_used=len(eligible),
        source_item_ids=[item.item_id for item in eligible],
        provider=response.provider,
        model=response.model,
        prompt_version=PROMPT_VERSIONS[view],
        scoring_version=SCORING_CONTRACT_VERSION,
    )
    storage.save_value_profile(profile)
    _logger.info(
        "value_profile persona_id=%s view=%s items=%d axes=%d rejected=%d provider=%s model=%s",
        persona_id,
        view.value,
        len(eligible),
        len(axes),
        len(rejected),
        response.provider,
        response.model,
    )
    return ValueProfileReport(profile=profile, rejected=rejected)


def new_captures_since(
    storage: Storage, profile: ValueProfile, persona_id: str | None = None
) -> int:
    """How many currently-eligible captures postdate `profile` (staleness,
    design question #6) — a plain set difference against
    `profile.source_item_ids`, deterministic and model-free. 0 means the
    stored profile still reflects every eligible capture; a caller wanting
    the update reflected has to explicitly rebuild (refresh=True), the
    same "stored until asked to rebuild" contract PovCard already uses.

    Eligibility is read off `profile.view` rather than passed in (#356):
    the two views have different eligible sets, and a caller that had to
    supply the view could supply the wrong one — which would report a work
    profile stale for every perspective reaction in the workspace, or
    never stale for any of them."""
    current_ids = {item.item_id for item in _eligible_items(storage, persona_id, profile.view)}
    return len(current_ids - set(profile.source_item_ids))


def _predates_direction(profile: ValueProfile) -> bool:
    """True for a profile stored before RFC-056 — its evidence carries no
    `direction`, so its scores came from the inverted sign-from-subtype
    rule (#340). Rebuilding is the fix, but nothing forces a rebuild (a
    stored profile is read as-is until asked to refresh), so the read
    surface has to say the numbers are suspect rather than show them
    plain. Deliberately checked on the evidence rather than on
    `prompt_version`: the evidence is what the score was computed from.

    Kept alongside `scoring_version` rather than replaced by it (#355):
    the version says a profile is superseded, this says HOW, and naming
    the specific damage ("an axis evidenced by a 'con' nomination may have
    the wrong sign") is worth more to a reader than a version string. It
    is also what stays true for the profiles that predate the stamp
    entirely, which are exactly the ones #340 broke."""
    return any(span.direction is None for axis in profile.axes for span in axis.evidence)


def scoring_is_current(profile: ValueProfile) -> bool:
    """Was this profile scored by the rule the codebase runs today?

    The code half of "is this still true" (#355), and the exact question
    nothing could answer when #340 landed. False for a profile stamped
    with a superseded version AND for one stamped with none at all —
    an unstamped profile predates the stamp, which means it predates
    #340's fix, which is the worst case, not the benign one. Silence is
    read as "unknown", never as "current".
    """
    return profile.scoring_version == SCORING_CONTRACT_VERSION


def parse_value_view(raw: str) -> ValueView:
    """One place that turns a CLI flag or an MCP argument into a `ValueView`.

    Refuses an unknown one by name rather than silently falling back to the
    character view: a typo that quietly builds the OTHER reading, under a
    heading that says so in small print, is how somebody ends up quoting
    character axes at a hiring conversation believing they are work ones.
    """
    cleaned = raw.strip().lower() or ValueView.CHARACTER.value
    try:
        return ValueView(cleaned)
    except ValueError as exc:
        known = ", ".join(view.value for view in ValueView)
        raise IngestError(
            f"unknown value view {raw!r}; use one of: {known}. Nothing was built."
        ) from exc


def refresh_command(view: ValueView) -> str:
    """The one command that rebuilds this view. Named once, here, because it
    is quoted by the renderer, the chart's notice rows, the freshness report
    and the fit brief — and a rebuild instruction that omits `--view work`
    rebuilds the wrong artefact while looking like it worked."""
    return (
        "wingman values --refresh"
        if view is ValueView.CHARACTER
        else f"wingman values --refresh --view {view.value}"
    )


def refresh_tool(view: ValueView) -> str:
    """The same instruction for a conversation rather than a shell."""
    return (
        "my_values(refresh=True)"
        if view is ValueView.CHARACTER
        else f"my_values(refresh=True, view={view.value!r})"
    )


def work_grounding_note(profile: ValueProfile) -> str:
    """What a WORK-view profile has to admit about its own evidence — or ""
    when there is nothing to admit.

    The work view's characteristic failure is not thin evidence, it is a
    leap of REGISTER: axes about how somebody works, read entirely off
    verdicts about people's character. The perspective reactions
    (`alignment_of_perspective_*`) are the captures that are actually about
    ideas, and a work profile with none of them is one where every axis was
    inferred at one remove.

    Disclosed, not refused, and that is a deliberate trade-off. A floor
    requiring reactions would refuse the work view on essentially every
    workspace that exists today, including the owner's — the reaction
    subtypes are the least-used part of the interview. A feature nobody can
    run is not a floor, it is a wall, and it would leave the character axes
    as the only thing a fit brief could reach for, which is the exact
    problem #356 was filed about. So the profile renders, names the gap,
    and says what closes it. The cost, stated: somebody who ignores this
    line cites work axes that rest on character evidence.

    Counted from the stored evidence's own subtypes rather than from a new
    field, so it cannot drift from what the axes actually cite.
    """
    if profile.view is not ValueView.WORK:
        return ""
    grounded = {
        span.item_id
        for axis in profile.axes
        for span in axis.evidence
        if span.subtype in WORK_GROUNDING_SUBTYPES
    }
    if grounded:
        return ""
    return (
        "(every axis above was read off nominations about PEOPLE — this workspace has no "
        "perspective reactions among the cited evidence, so the work reading is an inference "
        "at one remove. Capture reactions to things you read "
        "(interview_react 'alignment_of_perspective_agree'/'_disagree') and rebuild to ground "
        "it in evidence about ideas.)"
    )


def scoring_note(profile: ValueProfile) -> str:
    """The one line to show a reader of a profile scored under a rule this
    codebase no longer runs — or "" when it is current.

    At most one line, and the most specific one that applies. A profile
    with no `direction` on its evidence gets the named diagnosis and the
    named consequence (#340 inverted its signs); every other superseded
    profile gets the general form, because a future contract change has no
    way to know in advance what it will have broken. Two overlapping
    warnings on the same profile would train the reader to skip both.
    """
    if scoring_is_current(profile):
        return ""
    rebuild = refresh_command(profile.view)
    if _predates_direction(profile):
        return (
            "(this profile was built before per-item direction was recorded, so an axis "
            f"evidenced by a 'con' nomination may have the wrong sign — rebuild with "
            f"'{rebuild}' to correct it)"
        )
    built_under = profile.scoring_version or "an unrecorded scoring rule"
    return (
        f"(scored under {built_under}; the current scoring contract is "
        f"{SCORING_CONTRACT_VERSION}. The numbers above came from a rule this version of "
        f"wingman no longer runs — rebuild with '{rebuild}' to score them "
        "under the current one)"
    )


def _contested_directions(axis: ValueAxis) -> int:
    """How many of this axis's citations point the opposite way from the
    nomination they came from (#342).

    On an axis named as a value the person HOLDS, both halves of the
    interview should read as `supports`: admiring an exemplar, and
    condemning someone who violated it, are both arguments for the value.
    An `opposes` citation is therefore where a naming/direction mismatch
    would show itself — the shape #340 was.

    Deliberately a count, not a rule. Two legitimate shapes disagree here
    and neither may be blocked:

      - an axis genuinely named as a DISVALUE ("Growth at any human
        cost"), where every citation opposes it and should;
      - real ambivalence — admiring one person's risk-taking while
        condemning another's recklessness is a position, and the
        near-zero score already reports it.

    So this reports and lets a human judge. The sign still comes from the
    model's direction; nothing here overrides it.
    """
    return sum(1 for span in axis.evidence if span.direction is AxisDirection.OPPOSES)


def render_value_profile(profile: ValueProfile, stale_new_captures: int = 0) -> str:
    """Deterministic text rendering shared by the CLI and MCP surfaces."""
    lines = [
        f"{VIEW_TITLES[profile.view]}: {profile.subject_name}",
        # The scoring-rule version is printed unconditionally, not only when
        # it is stale: AGENTS.md requires a score to expose the rule that
        # produced it, and a version that only appears when something is
        # wrong teaches nobody what the normal case looks like.
        (
            f"(built from {profile.items_used} captured items, "
            f"{profile.provider}/{profile.model}, "
            f"scoring {profile.scoring_version or 'unrecorded'}, "
            f"{profile.generated_at.date().isoformat()})"
        ),
        "",
        "Axes:",
    ]
    for axis in profile.axes:
        lines.append(f"- {axis.name}  [{axis.score:+.2f} — {axis.label}]")
        if axis.description:
            lines.append(f"    {axis.description}")
        contested = _contested_directions(axis)
        if contested:
            noun = "capture" if contested == 1 else "captures"
            lines.append(
                f"    ⚠ worth checking: {contested} {noun} cited as OPPOSING this axis. That is "
                "correct if the axis names something you do not hold, or if you are genuinely "
                "of two minds — but on an axis named as a value you hold, both admiring "
                "someone and condemning someone argue FOR it."
            )
        for span in axis.evidence:
            intensity = f", {span.intensity}" if span.intensity else ""
            direction = f", {span.direction.value} this axis" if span.direction else ""
            lines.append(
                f'    "{span.quote}" — {span.subtype}: {span.target}{intensity}{direction}'
            )
            if span.value_statement:
                lines.append(f'      values: "{span.value_statement}"')
    contract = scoring_note(profile)
    if contract:
        lines.append("")
        lines.append(contract)
    grounding = work_grounding_note(profile)
    if grounding:
        lines.append("")
        lines.append(grounding)
    if stale_new_captures:
        noun = "capture" if stale_new_captures == 1 else "captures"
        lines.append("")
        lines.append(
            f"({stale_new_captures} new {noun} since this was built — rebuild to include them)"
        )
    return "\n".join(lines)
