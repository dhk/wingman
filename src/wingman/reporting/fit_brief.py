"""Render an opportunity's fit brief as Markdown and JSON, fully cited."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from wingman.domain.opportunity import FitVerdict, Opportunity
from wingman.domain.profile import ProfileItem
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


def render_fit_brief(
    opportunity: Opportunity,
    items: list[ProfileItem],
    config: Config,
    models: dict[str, str],
) -> tuple[Path, Path]:
    items_by_id = {item.item_id: item for item in items}
    # Stable ID suffix: distinct opportunities with similar titles must not
    # overwrite each other, while re-assessments keep the same filenames.
    slug = f"{_slug(opportunity.title)}-{opportunity.opportunity_id[:8]}"

    payload = {
        "metadata": {"models": models, "generated_at": datetime.now(UTC).isoformat()},
        "opportunity": opportunity.model_dump(mode="json"),
    }
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

    gaps = [a for a in opportunity.assessments if a.verdict is FitVerdict.GAP]
    unknowns = [a for a in opportunity.assessments if a.verdict is FitVerdict.UNKNOWN]
    lines.extend(["## Summary", ""])
    counts = {
        label: sum(1 for a in opportunity.assessments if a.verdict is verdict)
        for verdict, label in _VERDICT_LABEL.items()
    }
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
