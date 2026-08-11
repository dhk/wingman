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

**The minimum-data floor.** A person with a couple of nominations
shouldn't get a confident multi-axis profile — v3's chart would just be
noise. `MIN_ITEMS`/`MIN_SUBTYPES` (see below) gate inference outright,
`IngestError`, before any model call is made.

**Staleness.** A `ValueProfile` is a stored, rebuilt-on-demand artifact —
the same lifecycle `domain.pov.PovCard` already uses (a fresh call
replaces the prior one; no versioned history). `source_item_ids` records
exactly which captures fed the stored profile, so `new_captures_since`
answers "is this stale" deterministically (a set difference against the
currently-eligible items) without any model call — only a rebuild
(`refresh=True`) actually re-invokes the model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from wingman.agents.values_analyst import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_prompt,
    parse_value_axis_proposal,
)
from wingman.application.ingest import IngestError
from wingman.application.interview import SENTIMENT_INTENSITY_SUBTYPES
from wingman.application.pov import CORPUS_PERSON_ID, CORPUS_PERSON_NAME, persona_card_id
from wingman.domain.persona import Persona
from wingman.domain.profile import ItemStatus, ProfileItem, ProfileItemKind, SentimentIntensity
from wingman.domain.values import (
    AxisDirection,
    ValueAxis,
    ValueAxisEvidence,
    ValueAxisProposal,
    ValueProfile,
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
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _eligible_items(storage: Storage, persona_id: str | None) -> list[ProfileItem]:
    return [
        item
        for item in storage.list_profile_items()
        if item.kind is ProfileItemKind.INTERVIEW
        and item.status is ItemStatus.ACTIVE
        and item.persona_id == persona_id
        and item.subtype in SENTIMENT_INTENSITY_SUBTYPES
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


def build_value_profile(
    storage: Storage, provider: ModelProvider, persona: Persona | None = None
) -> ValueProfileReport:
    """Build (or rebuild) the value-dimension profile for the user's own
    captures, or (with persona set) a coached Persona's own captures
    instead — the same coaching-mode scoping `application.pov.build_own_pov`
    already uses ("my evidence and their point of view never mix",
    docs/COACHING-MODE-DESIGN.md, extended to this axis).

    Raises IngestError before any model call if the minimum-data floor
    (MIN_ITEMS/MIN_SUBTYPES) isn't met, or after the model call if fewer
    than MIN_AXES_REQUIRED axes survive validation — in both cases
    nothing is stored.
    """
    persona_id = persona.persona_id if persona is not None else None
    eligible = _eligible_items(storage, persona_id)
    subtypes_present = {item.subtype for item in eligible}
    if len(eligible) < MIN_ITEMS or len(subtypes_present) < MIN_SUBTYPES:
        subject = persona.name if persona is not None else "you"
        raise IngestError(
            f"not enough captured evidence to infer value dimensions for {subject} yet — "
            f"{len(eligible)} Values/Mission-alignment item(s) across "
            f"{len(subtypes_present)} subtype(s) captured; need at least {MIN_ITEMS} items "
            f"spanning at least {MIN_SUBTYPES} subtypes (e.g. both values_pro and values_con "
            "nominations, or add mission_alignment ones). Capture more with 'wingman "
            "interview' or interview_react, then try again."
        )
    eligible = _newest(eligible, MAX_ITEMS_FOR_INFERENCE)

    prompt = build_prompt(_items_block(eligible))
    response = provider.complete(ModelRequest(system=SYSTEM_PROMPT, prompt=prompt))
    proposal = parse_value_axis_proposal(response.text)
    axes, rejected = _validate_proposal(proposal, {item.item_id: item for item in eligible})
    if len(axes) < MIN_AXES_REQUIRED:
        raise IngestError(
            f"only {len(axes)} value axis(es) survived validation ({len(rejected)} "
            f"rejected) — need at least {MIN_AXES_REQUIRED} for a meaningful profile. "
            "Nothing was stored; capture more evidence or re-run to retry."
        )

    profile = ValueProfile(
        subject_id=persona_card_id(persona.persona_id) if persona is not None else CORPUS_PERSON_ID,
        subject_name=persona.name if persona is not None else CORPUS_PERSON_NAME,
        axes=axes,
        items_used=len(eligible),
        source_item_ids=[item.item_id for item in eligible],
        provider=response.provider,
        model=response.model,
        prompt_version=PROMPT_VERSION,
    )
    storage.save_value_profile(profile)
    _logger.info(
        "value_profile persona_id=%s items=%d axes=%d rejected=%d provider=%s model=%s",
        persona_id,
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
    same "stored until asked to rebuild" contract PovCard already uses."""
    current_ids = {item.item_id for item in _eligible_items(storage, persona_id)}
    return len(current_ids - set(profile.source_item_ids))


def _predates_direction(profile: ValueProfile) -> bool:
    """True for a profile stored before RFC-056 — its evidence carries no
    `direction`, so its scores came from the inverted sign-from-subtype
    rule (#340). Rebuilding is the fix, but nothing forces a rebuild (a
    stored profile is read as-is until asked to refresh), so the read
    surface has to say the numbers are suspect rather than show them
    plain. Deliberately checked on the evidence rather than on
    `prompt_version`: the evidence is what the score was computed from."""
    return any(span.direction is None for axis in profile.axes for span in axis.evidence)


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
        f"Value profile: {profile.subject_name}",
        (
            f"(built from {profile.items_used} captured items, "
            f"{profile.provider}/{profile.model}, {profile.generated_at.date().isoformat()})"
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
    if _predates_direction(profile):
        lines.append("")
        lines.append(
            "(this profile was built before per-item direction was recorded, so an axis "
            "evidenced by a 'con' nomination may have the wrong sign — rebuild with "
            "'wingman values --refresh' to correct it)"
        )
    if stale_new_captures:
        noun = "capture" if stale_new_captures == 1 else "captures"
        lines.append("")
        lines.append(
            f"({stale_new_captures} new {noun} since this was built — rebuild to include them)"
        )
    return "\n".join(lines)
