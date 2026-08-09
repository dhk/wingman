"""Completeness, rendered pretty: a styled HTML twin of completeness.md.

Same pattern as `digest_html.py`: built from the run's structured
`CompletenessReport` directly, not by parsing the Markdown back. Reuses the
shared design tokens (`export.WINGMAN_PDF_CSS`) so this looks like every
other Wingman artifact — no separate palette. Self-contained (inline CSS),
opens anywhere, attaches cleanly (RFC-009: nothing fetched at render time).
"""

from __future__ import annotations

import html

from wingman.application.completeness import CompletenessReport, PersonCompleteness
from wingman.reporting.completeness import BLOCKED_SECTIONS
from wingman.reporting.export import WINGMAN_PDF_CSS

_COMPLETENESS_CSS = """
.completeness { max-width: 760px; margin: 0 auto; padding: 48px 24px 64px; }
.meter-row { display: flex; align-items: center; gap: 12px; padding: 6px 0; }
.meter-label { flex: 0 0 200px; font-size: 14px; }
.meter-track { flex: 1; height: 8px; border-radius: 4px; background: var(--bg3);
  overflow: hidden; }
.meter-fill { height: 100%; background: var(--accent); border-radius: 4px; }
.meter-value { flex: 0 0 auto; font-size: 12px; color: var(--text-dim);
  font-family: var(--font-mono); min-width: 3ch; text-align: right; }
.sub-list { margin: 8px 0 20px; padding-left: 0; list-style: none; }
.sub-list li { padding: 4px 0; border-top: 1px solid var(--border-light);
  font-size: 13px; color: var(--text-muted); }
.sub-list li:first-child { border-top: none; }
.sub-list b { color: var(--text); font-weight: 600; }
.blocked { border-left: 3px solid var(--accent-orange); padding: 8px 0 8px 16px;
  margin: 8px 0; color: var(--text-muted); font-size: 13px; }
"""


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _meter(label: str, value: int, target: int | None = None) -> str:
    """One meter row. Without a known target, the bar reads full when
    value > 0 and empty at 0 — there is no fake percentage to compute
    against an invented denominator (AGENTS.md invariant 9)."""
    if target:
        pct = min(100, round(100 * value / target))
        shown = f"{value}/{target}"
    else:
        pct = 100 if value else 0
        shown = str(value)
    return (
        '<div class="meter-row">'
        f'<span class="meter-label">{_e(label)}</span>'
        f'<div class="meter-track"><div class="meter-fill" style="width:{pct}%"></div></div>'
        f'<span class="meter-value">{_e(shown)}</span>'
        "</div>"
    )


def _person_line(person: PersonCompleteness) -> str:
    where = f" ({_e(person.company)})" if person.company else ""
    link = "linked" if person.linked else "no LinkedIn/feed"
    logged = f"{person.log_entries} log entr{'y' if person.log_entries == 1 else 'ies'}"
    return f"<li><b>{_e(person.name)}</b>{where} — {link}, {logged}</li>"


def render_completeness_html(report: CompletenessReport) -> str:
    career = report.career
    body: list[str] = [
        f"<h1>Completeness — {report.generated_at.date().isoformat()}</h1>",
        '<div class="meta">local data only — no fetch, no model call</div>',
        '<div class="section-divider"><span>Career Profile</span></div>',
        _meter("Roles", career.roles),
        _meter("Achievements", career.achievements),
        _meter("Skills", career.skills),
        _meter("Testimonials", career.testimonials),
        '<div class="section-divider"><span>Job Criteria</span></div>',
    ]
    if report.job_criteria.exists:
        body.append('<p class="dim">job-criteria.md exists — openings are scored against it.</p>')
    else:
        body.append(
            '<p class="dim">job-criteria.md does not exist yet — openings are scored '
            "unweighted. Call job_criteria(action='review') to start the seeding "
            "interview.</p>"
        )
    body.append(
        f'<div class="section-divider"><span>People ({len(report.people)} tracked)</span></div>'
    )
    if report.people:
        ordered = sorted(report.people, key=lambda p: (p.log_entries, p.linked))
        body.append('<ul class="sub-list">')
        body.extend(_person_line(person) for person in ordered)
        body.append("</ul>")
    else:
        body.append('<p class="dim">None yet.</p>')
    body.append(
        f'<div class="section-divider"><span>Companies '
        f"({len(report.companies)} attributable)</span></div>"
    )
    if report.companies:
        for company in report.companies:
            body.append(f"<h3>{_e(company.name)}</h3>")
            body.append(_meter("People watched", company.people_watched))
            body.append(_meter("Documents", company.documents))
            body.append(
                _meter(
                    "POV cards",
                    company.pov_cards,
                    target=company.people_watched or None,
                )
            )
    else:
        body.append('<p class="dim">None yet — no watched person has a company set.</p>')
    body.append('<div class="section-divider"><span>Blocked</span></div>')
    for name, reason in BLOCKED_SECTIONS:
        body.append(f'<div class="blocked">⛔ <b>{_e(name)}</b> — {_e(reason)}</div>')
    joined = "\n".join(body)
    return (
        "<!doctype html>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Completeness — {report.generated_at.date().isoformat()}</title>\n"
        f"<style>\n{WINGMAN_PDF_CSS}\n{_COMPLETENESS_CSS}</style>\n"
        f'<div class="completeness">\n{joined}\n</div>\n'
    )
