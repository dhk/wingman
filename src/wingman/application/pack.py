"""Application packs: everything the workspace knows, composed for one role (RFC-024).

The bridge from hiring signal to application. A pack takes an assessed
opportunity and composes — deterministically, no model call — the cited fit
summary, your strongest evidence as cover-letter fodder (verbatim quotes
from your own profile evidence, for you to write in your own voice), and
the company intelligence already validated in the workspace: synthesized
themes, people you know there, approved research sources. Wingman still
never writes your letter and never sends anything (RFC-006).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.application.pov import company_card_id
from wingman.application.similarity import company_key, infer_company_from_title
from wingman.domain.opportunity import FitVerdict, Opportunity
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.pack")

_VERDICT_MARK = {
    FitVerdict.MET: "✓",
    FitVerdict.PARTIAL: "◐",
    FitVerdict.GAP: "✗",
    FitVerdict.UNKNOWN: "?",
}


class PackReport(BaseModel):
    title: str
    company: str | None
    path: str
    markdown: str


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:60] or "pack"


def _find_opportunity(query: str, storage: Storage) -> Opportunity:
    opportunities = storage.list_opportunities()
    if not opportunities:
        raise IngestError(
            "no assessed opportunities in the workspace — run "
            "'wingman assess <job.md>' or 'wingman assess --url <posting>' first."
        )
    needle = " ".join(query.lower().split())
    matches = [entry for entry in opportunities if needle in entry.title.lower()]
    if not matches:
        titles = "; ".join(entry.title for entry in opportunities[-5:])
        raise IngestError(f"no assessed opportunity matches {query!r}. Recent: {titles}")
    return max(matches, key=lambda entry: entry.created_at)


def build_application_pack(
    query: str,
    config: Config,
    storage: Storage,
    company: str | None = None,
    out_dir: Path | None = None,
) -> PackReport:
    """Compose and write the pack for one assessed role. Local data only."""
    opportunity = _find_opportunity(query, storage)
    company_name = company or infer_company_from_title(opportunity.title, storage)
    items = {item.item_id: item for item in storage.list_profile_items()}
    requirements = {req.requirement_id: req for req in opportunity.requirements}
    now = datetime.now(UTC)

    lines = [
        f"# Application pack: {opportunity.title}",
        "",
        (
            f"Generated {now.date().isoformat()} · assessed "
            f"{opportunity.created_at.date().isoformat()} · next action: {opportunity.next_action}"
        ),
        "",
        "## Fit, requirement by requirement",
        "",
    ]
    counts: dict[str, int] = {}
    fodder: list[str] = []
    for assessment in opportunity.assessments:
        requirement = requirements.get(assessment.requirement_id)
        if requirement is None:
            continue
        verdict = assessment.verdict
        counts[verdict.value] = counts.get(verdict.value, 0) + 1
        mark = _VERDICT_MARK.get(verdict, "?")
        cited = [items[item_id] for item_id in assessment.evidence_item_ids if item_id in items]
        cited_names = ", ".join(item.name for item in cited) or "—"
        lines.append(f"- {mark} **{requirement.name}** ({verdict.value}) — {assessment.rationale}")
        lines.append(f"  evidence: {cited_names}")
        if verdict in (FitVerdict.MET, FitVerdict.PARTIAL):
            for item in cited[:1]:
                quote = item.evidence[0].quote if item.evidence else ""
                if quote:
                    fodder.append(f'- **{requirement.name}** → {item.name}: "{quote}"')
    summary = "  ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
    lines.insert(5, f"{summary}")
    lines.insert(6, "")

    lines.extend(
        ["", "## Cover-letter fodder (compose in your own voice — Wingman never sends)", ""]
    )
    if fodder:
        lines.extend(fodder)
    else:
        lines.append(
            "- No met/partial requirements carry citable evidence yet — "
            "strengthen the profile ('wingman ingest') and re-assess."
        )

    if company_name:
        key = company_key(company_name)
        lines.extend(["", f"## What the workspace knows about {company_name}", ""])
        card = storage.get_pov_card(company_card_id(key))
        if card is not None:
            for stance in card.stances:
                tag = f"[{stance.dimension.value}] " if stance.dimension else ""
                lines.append(f"- [inference] {tag}{stance.statement}")
                lines.append(f'  [fact] "{stance.quote}" ({stance.doc_title})')
        else:
            lines.append(f"- no synthesized themes yet — 'wingman company pov \"{company_name}\"'")
        people_there = [
            person
            for person in storage.list_people()
            if person.company and company_key(person.company) == key
        ]
        if people_there:
            lines.append("")
            lines.append("People you know there:")
            for person in sorted(people_there, key=lambda entry: entry.name):
                position = f" — {person.position}" if person.position else ""
                lines.append(f"- {person.name}{position}")
        sources = storage.list_company_sources(key)
        if sources:
            lines.append("")
            lines.append("Approved research sources (run 'wingman company research'):")
            lines.extend(f"- {source.url}" for source in sources)
    else:
        lines.extend(
            [
                "",
                "## Company",
                "",
                (
                    "- could not infer the company from the role title — "
                    're-run with --company "<name>" to attach company intelligence'
                ),
            ]
        )

    from wingman.reporting.export import STYLESHEET_NAME, WINGMAN_PDF_CSS, _frontmatter

    directory = (out_dir.expanduser() if out_dir else config.reports_dir / "packs").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / STYLESHEET_NAME).write_text(WINGMAN_PDF_CSS, encoding="utf-8")
    markdown = (
        _frontmatter(f"Application pack — {opportunity.title}", directory)
        + "\n".join(lines).rstrip()
        + "\n"
    )
    path = directory / f"pack-{_slug(opportunity.title)}-{now.date().isoformat()}.md"
    path.write_text(markdown, encoding="utf-8")
    _logger.info(
        "pack title=%s company=%s fodder=%d path=%s",
        opportunity.title,
        company_name,
        len(fodder),
        path,
    )
    return PackReport(
        title=opportunity.title, company=company_name, path=str(path), markdown=markdown
    )
