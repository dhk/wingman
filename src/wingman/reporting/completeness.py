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

from wingman.application.completeness import (
    CompanyCompleteness,
    CompletenessReport,
    compute_completeness,
)
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage

# Sections this report cannot honestly measure yet. Empty today: both
# original entries shipped — #311 as `interview_status`, #312 as
# `opportunities_list` — and this tuple went on declaring them impossible
# long afterwards, so the one place somebody looks for progress told them
# the data was unavailable while the tools sat right there. Kept as the
# seam for the next genuinely unmeasurable section, and as a reminder that
# an entry here is a claim with an expiry date.
BLOCKED_SECTIONS: tuple[tuple[str, str], ...] = ()

# How many curated people the report names inline. A tool response has to stay
# readable however large the workspace gets (#465 saw a 312k-character report),
# so beyond this the rest are a count. The neediest come first.
MAX_PEOPLE_LISTED = 50

# The same bound for Companies: 2,000 curated people at distinct employers
# would otherwise render 2,000 rows (~150k characters of Markdown, ~1.2M of HTML).
MAX_COMPANIES_LISTED = 50


def people_heading(report: CompletenessReport) -> str:
    """The People section title. It counts followed people only (#624); 'tracked'
    once read '(0 tracked)' above a workspace holding thousands of people."""
    return f"People ({len(report.people)} followed)"


def companies_listed(report: CompletenessReport) -> tuple[list[CompanyCompleteness], int]:
    """The companies to name inline, neediest (most missing POV cards) first,
    and how many more there are. Shared by both renderers so the cap and the
    order cannot drift."""
    ordered = sorted(report.companies, key=lambda c: (-c.missing_pov_cards, c.name))
    return ordered[:MAX_COMPANIES_LISTED], max(0, len(ordered) - MAX_COMPANIES_LISTED)


def unfollowed_people_line(count: int) -> str:
    """The one line that makes everyone else visible without making them a gap.
    Shared by the Markdown and HTML renderers so they cannot drift."""
    noun = "person" if count == 1 else "people"
    return (
        f"{count} other {noun} you are not following — not listed or counted as gaps. "
        "people add, or watchlist add, brings someone into this list."
    )


def _todo_markdown(report: CompletenessReport) -> list[str]:
    from wingman.application.completeness import next_actions

    actions = next_actions(report)
    if not actions:
        return [
            "## Things to do",
            "",
            "Nothing outstanding — every section has something in it.",
            "",
        ]
    lines = ["## Things to do", ""]
    for number, action in enumerate(actions, start=1):
        lines.append(f"{number}. **{action.title}** — {action.why}")
        lines.append(f"   → {action.how}")
        # An operator action is somebody's instruction, not a measurement
        # of this workspace (issue #224). It never renders like the others.
        if action.attribution:
            lines.append(f"   _{action.attribution}_")
    lines.append("")
    return lines


def render_completeness_markdown(report: CompletenessReport) -> str:
    lines = [
        "# Completeness",
        "",
        (
            f"Generated: {report.generated_at.date().isoformat()} "
            "(local data only — no fetch, no model call)"
        ),
        "",
        *_todo_markdown(report),
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
        f"## {people_heading(report)}",
        "",
    ]
    if report.people:
        ordered = sorted(report.people, key=lambda p: (p.log_entries, p.linked), reverse=False)
        for person in ordered[:MAX_PEOPLE_LISTED]:
            where = f" ({person.company})" if person.company else ""
            link = "linked" if person.linked else "no LinkedIn/feed"
            logged = f"{person.log_entries} log entr{'y' if person.log_entries == 1 else 'ies'}"
            lines.append(f"- {person.name}{where} — {link}, {logged}")
        if len(ordered) > MAX_PEOPLE_LISTED:
            lines.append(f"- …and {len(ordered) - MAX_PEOPLE_LISTED} more")
    else:
        lines.append("_None yet._")
    if report.unfollowed_people:
        lines.extend(["", f"_{unfollowed_people_line(report.unfollowed_people)}_"])
    lines.extend(["", "## Interview", ""])
    current = ""
    for row in report.interview:
        if row.category != current:
            current = row.category
            lines.append(f"**{current}**")
        lines.append(
            f"- {row.subtype}: {row.count}/{row.cap} ({'captured' if row.count else 'not yet'})"
        )
    assessed = report.opportunities.assessed
    lines.extend(
        [
            "",
            "## Applications",
            "",
            (
                f"- {assessed} opportunit{'y' if assessed == 1 else 'ies'} assessed"
                " — opportunities_list names them."
            ),
        ]
    )
    lines.extend(["", f"## Companies ({len(report.companies)} attributable)", ""])
    if report.companies:
        shown, more = companies_listed(report)
        for company in shown:
            lines.append(
                f"- {company.name} — {company.people_watched} people watched, "
                f"{company.documents} documents, {company.pov_cards} POV cards "
                f"({company.missing_pov_cards} missing)"
            )
        if more:
            lines.append(f"- …and {more} more")
    else:
        lines.append("_None yet — follow a company with company_follow or watchlist add._")
    if BLOCKED_SECTIONS:
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
