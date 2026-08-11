"""Render an opportunity's fit brief as Markdown and JSON, fully cited.

**How you want to work (issue #356, RFC-066).** The brief argues whether a
role suits somebody, and until now the only thing it could argue from was
requirement-by-requirement evidence: what the posting asks for, and which
profile item answers it. The other half of "does this suit me" — the
conditions somebody needs, what they want authority over, the standard they
hold work to — was captured, inferred, scored and charted, and then read by
nobody, because the axes were named as character traits no fit argument
could cite without a leap.

The work view (`domain.values.ValueView.WORK`) is named in work terms, so
this renders it here, next to the verdicts, cited to the same captures.

**Deterministic, and deliberately not in the assessment prompt.** No model
sees these axes. A requirement's verdict is still MET only when a profile
item resolves for it (`application.assess._validate_assessments`), and
feeding an inferred axis to the assessing model would let "verification is
a precondition for shipping" launder itself into evidence that a candidate
MEETS a testing requirement — an inference standing in for a fact, which is
the failure this codebase's whole evidence discipline exists to prevent. So
the axes are SHOWN, with their scores and their citations, and a human
makes the argument. The brief gains a section, not an opinion.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from wingman.domain.opportunity import FitVerdict, Opportunity, count_verdicts
from wingman.domain.profile import ProfileItem
from wingman.domain.values import ValueProfile
from wingman.infrastructure.config import Config

_VERDICT_LABEL = {
    FitVerdict.MET: "Met",
    FitVerdict.PARTIAL: "Partial",
    FitVerdict.GAP: "Gap",
    FitVerdict.UNKNOWN: "Unknown",
}


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "opportunity"


def _work_profile_lines(profile: ValueProfile, new_captures: int) -> list[str]:
    """The work-view section: every axis, its signed score, and the captures
    it cites — plus anything the profile has to admit about itself.

    Local imports because `reporting` may not import `application` at module
    scope (RFC-001 layering); these are read-only renderers of a stored
    artefact, called once per brief.
    """
    from wingman.application.values import refresh_command, scoring_note, work_grounding_note

    lines = [
        "## How you want to work",
        "",
        (
            "Inferred from your own captured interview reactions "
            f"({profile.items_used} items, {profile.provider}/{profile.model}, scoring "
            f"{profile.scoring_version or 'unrecorded'}, "
            f"{profile.generated_at.date().isoformat()}). Every axis cites the captures it "
            "was read from. Nothing here was used to decide a verdict above — these are "
            "conditions to weigh the role against, not evidence that a requirement is met."
        ),
        "",
    ]
    for axis in profile.axes:
        lines.append(f"### {axis.name} ({axis.score:+.2f} — {axis.label})")
        lines.append("")
        if axis.description:
            lines.extend([axis.description, ""])
        for span in axis.evidence:
            direction = f", {span.direction.value}" if span.direction else ""
            lines.append(f"- **{span.subtype}: {span.target}**{direction} (`{span.item_id}`)")
            lines.append(f"  > {span.quote}")
            if span.value_statement:
                lines.append(f"  > values: {span.value_statement}")
        lines.append("")
    # Every caveat the profile carries follows it into the brief. A brief is
    # exactly the document that gets forwarded to somebody who will never
    # see the tool that produced it, so a warning left behind at the console
    # is a warning that reached nobody who acts on it.
    for caveat in (scoring_note(profile), work_grounding_note(profile)):
        if caveat:
            lines.extend([caveat, ""])
    if new_captures:
        noun = "capture" if new_captures == 1 else "captures"
        lines.extend(
            [
                (
                    f"({new_captures} new {noun} since this was built — "
                    f"'{refresh_command(profile.view)}' to include them)"
                ),
                "",
            ]
        )
    return lines


def render_fit_brief(
    opportunity: Opportunity,
    items: list[ProfileItem],
    config: Config,
    models: dict[str, str],
    work_profile: ValueProfile | None = None,
    work_new_captures: int = 0,
) -> tuple[Path, Path]:
    """`work_profile` is the stored WORK-view value profile, or None when the
    workspace has not built one — in which case the brief is exactly what it
    was before, rather than carrying an empty section that reads as "you
    want nothing in particular"."""
    items_by_id = {item.item_id: item for item in items}
    # Stable ID suffix: distinct opportunities with similar titles must not
    # overwrite each other, while re-assessments keep the same filenames.
    slug = f"{_slug(opportunity.title)}-{opportunity.opportunity_id[:8]}"

    payload: dict[str, object] = {
        "metadata": {"models": models, "generated_at": datetime.now(UTC).isoformat()},
        "opportunity": opportunity.model_dump(mode="json"),
    }
    if work_profile is not None:
        payload["work_profile"] = work_profile.model_dump(mode="json")
    brief_json = config.reports_dir / f"fit-brief-{slug}.json"
    brief_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    requirements_by_id = {r.requirement_id: r for r in opportunity.requirements}
    lines = [
        f"# Fit Brief: {opportunity.title}",
        "",
        f"Status: {opportunity.status.value}",
        f"Next action: {opportunity.next_action}",
        "",
        "## Requirements",
        "",
    ]
    for assessment in opportunity.assessments:
        requirement = requirements_by_id[assessment.requirement_id]
        confidence = f"confidence {assessment.confidence:.2f}"
        lines.append(
            f"### {_VERDICT_LABEL[assessment.verdict]} — {requirement.name}"
            f" ({requirement.kind.value}, {confidence})"
        )
        lines.append("")
        if requirement.detail:
            lines.append(f"{requirement.detail}")
        lines.append("Job description asks:")
        for span in requirement.evidence:
            for line in span.quote.splitlines() or [span.quote]:
                lines.append(f"> {line}")
        if assessment.rationale:
            lines.append("")
            lines.append(assessment.rationale)
        if assessment.evidence_item_ids:
            lines.append("")
            lines.append("Evidence from your profile:")
            for item_id in assessment.evidence_item_ids:
                item = items_by_id.get(item_id)
                if item is None:
                    continue
                detail = f" — {item.detail}" if item.detail else ""
                lines.append(f"- **{item.name}**{detail} (`{item_id}`)")
                for span in item.evidence:
                    for line in span.quote.splitlines() or [span.quote]:
                        lines.append(f"  > {line}")
        lines.append("")

    # Between the requirement verdicts and the summary: after what the role
    # asks and how the evidence answers it, before the verdict counts a
    # reader takes away. First would put working conditions ahead of the
    # posting's own requirements; last would file them after the conclusion.
    if work_profile is not None:
        lines.extend(_work_profile_lines(work_profile, work_new_captures))

    gaps = [a for a in opportunity.assessments if a.verdict is FitVerdict.GAP]
    unknowns = [a for a in opportunity.assessments if a.verdict is FitVerdict.UNKNOWN]
    lines.extend(["## Summary", ""])
    counts_by_verdict = count_verdicts(opportunity.assessments)
    counts = {label: counts_by_verdict[verdict] for verdict, label in _VERDICT_LABEL.items()}
    lines.append(" · ".join(f"{label}: {count}" for label, count in counts.items()))
    if gaps:
        lines.extend(
            ["", "Gaps: " + ", ".join(requirements_by_id[a.requirement_id].name for a in gaps)]
        )
    if unknowns:
        lines.extend(
            [
                "",
                "Uncertain: "
                + ", ".join(requirements_by_id[a.requirement_id].name for a in unknowns),
            ]
        )
    lines.append("")
    brief_md = config.reports_dir / f"fit-brief-{slug}.md"
    brief_md.write_text("\n".join(lines), encoding="utf-8")
    return brief_json, brief_md
