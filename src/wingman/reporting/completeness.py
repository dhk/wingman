"""Render the completeness snapshot as completeness.json and completeness.md.

Same posture as `reporting/career.py`: compute once, write both a machine-
readable JSON payload and a human-readable Markdown report under
reports/. The Markdown is the canonical, greppable artifact; `completeness_html.py`
renders the HTML twin from this module's structured `CompletenessReport`
directly — never by parsing the Markdown back (same rule `digest_html.py`
follows).

Interview Bootstrap and Applications are reported as BLOCKED, not zero —
see `application/completeness.py`'s module docstring for why.
"""

from __future__ import annotations

from pathlib import Path

from wingman.application.completeness import CompletenessReport, compute_completeness
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage

BLOCKED_SECTIONS = (
    (
        "Interview Bootstrap",
        "no tool reads back interview_react captures yet — see issue #311",
    ),
    (
        "Applications",
        "status counts opportunities but nothing lists them by name yet — see issue #312",
    ),
)


def render_completeness_markdown(report: CompletenessReport) -> str:
    lines = [
        "# Completeness",
        "",
        f"Generated: {report.generated_at.date().isoformat()} "
        "(local data only — no fetch, no model call)",
        "",
        "## Career Profile",
        "",
        f"- Roles: {report.career.roles}",
        f"- Achievements: {report.career.achievements}",
        f"- Skills: {report.career.skills}",
        f"- Testimonials: {report.career.testimonials}"
        + ("" if report.career.testimonials else " — none captured yet"),
        "",
        "## Job Criteria",
        "",
        "- job-criteria.md exists, openings are scored against it"
        if report.job_criteria.exists
        else "- job-criteria.md does not exist yet — openings are scored unweighted. "
        "Call job_criteria(action='review') to start the seeding interview.",
        "",
        f"## People ({len(report.people)} tracked)",
        "",
    ]
    if report.people:
        for person in sorted(report.people, key=lambda p: (p.log_entries, p.linked), reverse=False):
            where = f" ({person.company})" if person.company else ""
            link = "linked" if person.linked else "no LinkedIn/feed"
            logged = f"{person.log_entries} log entr{'y' if person.log_entries == 1 else 'ies'}"
            lines.append(f"- {person.name}{where} — {link}, {logged}")
    else:
        lines.append("_None yet._")
    lines.extend(["", f"## Companies ({len(report.companies)} attributable)", ""])
    if report.companies:
        for company in report.companies:
            lines.append(
                f"- {company.name} — {company.people_watched} people watched, "
                f"{company.documents} documents, {company.pov_cards} POV cards "
                f"({company.missing_pov_cards} missing)"
            )
    else:
        lines.append("_None yet — no watched person has a company set._")
    lines.extend(["", "## Blocked", ""])
    for name, reason in BLOCKED_SECTIONS:
        lines.append(f"- {name}: {reason}")
    lines.append("")
    return "\n".join(lines)


def write_completeness(storage: Storage, config: Config) -> tuple[CompletenessReport, Path, Path]:
    """Compute the current snapshot and write it to reports/completeness.{json,md}.

    Returns the report itself (for a caller that also wants the HTML twin,
    e.g. the MCP tool) plus the two written paths.
    """
    report = compute_completeness(storage, config)
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = config.reports_dir / "completeness.json"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path = config.reports_dir / "completeness.md"
    md_path.write_text(render_completeness_markdown(report), encoding="utf-8")
    return report, json_path, md_path
