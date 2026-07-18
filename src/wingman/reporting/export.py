"""Print-ready exports: Letter-format artifacts styled by the DHK design system.

Each export writes a Markdown file (with md-to-pdf frontmatter) plus the
shared `wingman-pdf.css` into reports/pdf/. Rendering to PDF happens in the
user's own pipeline (`md-to-pdf reports/pdf/<file>.md`) — wingman ships the
content and the stylesheet, never a browser engine. All styling values are
derived from the website design-system tokens; fonts are expected locally
(Barlow, Barlow Condensed, DM Mono) with honest fallbacks — nothing is ever
fetched at render time (RFC-009).

Three artifacts:
- career: portrait one-pager from the canonical profile, every claim cited.
- company: the company dossier, labels upgraded to styled tags.
- person: a landscape three-column sheet — outreach brief | point of view |
  related links — with clickable URLs wherever the data has one.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from pathlib import Path

from wingman.application.dossier import build_company_dossier
from wingman.application.ingest import IngestError
from wingman.application.similarity import company_key, similar_people
from wingman.domain.person import FeedAttribution, Person, PersonOrigin
from wingman.domain.profile import ItemStatus, ProfileItemKind
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("reporting.export")

STYLESHEET_NAME = "wingman-pdf.css"

# Derived from the DHK website design system. US Letter (user decision, not
# the site doc's A4); margins tuned for Letter's shorter page.
WINGMAN_PDF_CSS = """\
/* wingman-pdf.css — derived from the DHK website design-system tokens.
   Fonts are expected locally; the stacks degrade honestly. */
:root {
  --bg: #f9f8f6; --bg2: #f2f1ee; --bg3: #eae9e5;
  --border: #dddbd6; --border-light: #e8e6e1;
  --text: #0a0a09; --text-head: #0a0a09; --text-muted: #111110; --text-dim: #5a5850;
  --accent: #16a34a; --accent-blue: #2970d6;
  --accent-purple: #7c5ce0; --accent-orange: #d94f2a;
  --font-sans: Barlow, "Helvetica Neue", Arial, sans-serif;
  --font-cond: "Barlow Condensed", "Arial Narrow", Barlow, sans-serif;
  --font-mono: "DM Mono", ui-monospace, "SF Mono", Menlo, monospace;
  --border-radius: 4px;
}
body {
  font-family: var(--font-sans); font-weight: 300; line-height: 1.75;
  color: var(--text); background: var(--bg); margin: 0;
}
.portrait { max-width: 680px; }
h1 {
  font-family: var(--font-cond); font-weight: 700; font-size: 42px;
  letter-spacing: -0.01em; color: var(--text-head); margin: 0 0 8px;
}
h2 {
  font-family: var(--font-cond); font-weight: 600; font-size: 28px;
  letter-spacing: 0.01em; color: var(--text-head); margin: 48px 0 16px;
}
h3 { font-family: var(--font-cond); font-weight: 600; font-size: 20px; margin: 32px 0 12px; }
p { margin: 0 0 24px; }
ul { padding-left: 20px; margin: 0 0 24px; }
li { margin-bottom: 8px; }
a { color: var(--accent-blue); text-decoration: none; }
blockquote {
  border-left: 3px solid var(--accent); padding: 2px 0 2px 24px;
  margin: 12px 0 24px; color: var(--text-muted); font-style: italic;
}
code {
  font-family: var(--font-mono); font-size: 13px; background: var(--bg3);
  padding: 2px 6px; border-radius: var(--border-radius); color: var(--accent);
}
.meta {
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--text-dim); margin: 0 0 40px;
}
.section-divider {
  display: flex; align-items: center; gap: 20px;
  padding: 40px 0 24px; border-top: 1px solid var(--border); margin-top: 40px;
}
.section-divider span {
  font-family: var(--font-mono); font-size: 10px; letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--text-dim); white-space: nowrap;
}
.section-divider::after { content: ""; flex: 1; border-top: 1px solid var(--border); }
.tag {
  font-family: var(--font-mono); font-size: 9px; text-transform: uppercase;
  letter-spacing: 0.1em; padding: 2px 8px; border-radius: var(--border-radius);
  display: inline-block; vertical-align: middle;
}
.tag-fact { background: rgba(22, 163, 74, 0.10); color: #15803d; }
.tag-inference { background: rgba(124, 92, 224, 0.10); color: var(--accent-purple); }
.tag-warn { background: rgba(217, 79, 42, 0.10); color: var(--accent-orange); }
.tag-skill { background: rgba(22, 163, 74, 0.10); color: #15803d; margin: 0 6px 6px 0; font-size: 10px; }
.card { border-left: 3px solid var(--accent); padding: 2px 0 2px 16px; margin: 0 0 24px; }
.card blockquote { border: none; padding: 0; margin: 8px 0 0; }
.dim { color: var(--text-dim); }
.meta-link { color: var(--accent-blue); }
/* person sheet: landscape three-column grid */
.sheet { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 32px; align-items: start; }
.sheet h2 { margin-top: 0; padding-bottom: 8px; border-bottom: 1px solid var(--border); }
.sheet ul { padding-left: 18px; }
.their { border-left: 3px solid var(--accent-purple); padding: 2px 0 2px 14px; margin: 0 0 8px; }
.yours { border-left: 3px solid var(--accent); padding: 2px 0 2px 14px; margin: 0 0 8px; font-style: italic; color: var(--text-muted); }
.point { margin: 0 0 28px; }
.draft-panel {
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: var(--border-radius); padding: 20px; margin-top: 24px;
}
.draft-panel .meta { margin-bottom: 12px; }
.stance { margin: 0 0 24px; }
.warmth {
  font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.08em;
  text-transform: uppercase; margin: 0 0 6px;
}
.warmth-hot, .warmth-warm { color: var(--accent); }
.warmth-cool { color: var(--accent-blue); }
.warmth-cold { color: var(--text-dim); }
/* stance dimensions: what kind of concordance a point offers */
.tag-values { background: rgba(22, 163, 74, 0.10); color: #15803d; }
.tag-attitude { background: rgba(124, 92, 224, 0.10); color: var(--accent-purple); }
.tag-technical { background: rgba(41, 112, 214, 0.10); color: var(--accent-blue); }
.tag-strategy { background: rgba(217, 79, 42, 0.10); color: var(--accent-orange); }
"""


def _dimension_tag(dimension: object) -> str:
    """A styled chip for a StanceDimension, empty for uncategorized."""
    value = getattr(dimension, "value", None)
    if not value:
        return ""
    return f'<span class="tag tag-{value}">{value}</span> '


def _resolve_out_dir(config: Config, out_dir: Path | None) -> Path:
    """The export destination: --out when given, reports/pdf/ otherwise."""
    return (out_dir.expanduser() if out_dir is not None else config.reports_dir / "pdf").resolve()


def _frontmatter(title: str, directory: Path, landscape: bool = False) -> str:
    margin = "14mm 16mm" if landscape else "32mm 28mm"
    # md-to-pdf resolves the stylesheet relative to the process cwd, not the
    # markdown file — so the frontmatter carries the absolute path (quoted:
    # the default workspace lives under "Application Support").
    stylesheet = directory / STYLESHEET_NAME
    lines = [
        "---",
        f"title: {title}",
        "pdf_options:",
        "  format: Letter",
        f"  margin: {margin}",
        "  printBackground: true",
    ]
    if landscape:
        lines.append("  landscape: true")
    lines.extend([f'stylesheet: "{stylesheet}"', "---", ""])
    return "\n".join(lines)


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return cleaned or "export"


def _e(text: str) -> str:
    """Escape untrusted content for the HTML fragments in the export."""
    return html.escape(text, quote=True)


def _link(url: str, label: str | None = None) -> str:
    return f'<a class="meta-link" href="{_e(url)}">{_e(label or url)}</a>'


def _warmth(person: Person, common_count: int) -> tuple[int, str, list[str]]:
    """A transparent warmth score: every point names the signal behind it.

    Deterministic arithmetic over what the workspace actually knows — a
    direct LinkedIn connection, an email on file, shared-company
    connections. No model call, no guessing.
    """
    score = 0
    signals: list[str] = []
    if person.connected_on or person.origin is PersonOrigin.LINKEDIN_CONNECTIONS:
        score += 2
        since = f" since {person.connected_on}" if person.connected_on else ""
        signals.append(f"direct connection{since}")
    if person.email:
        score += 1
        signals.append("email on file")
    if common_count:
        score += 2 if common_count >= 3 else 1
        plural = "s" if common_count != 1 else ""
        signals.append(f"{common_count} shared-company connection{plural}")
    if not signals:
        signals.append("no direct path known — warm it up through the writing")
    label = "cold" if score == 0 else "cool" if score == 1 else "warm" if score <= 3 else "hot"
    return score, label, signals


def _write(directory: Path, filename: str, markdown: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / STYLESHEET_NAME).write_text(WINGMAN_PDF_CSS, encoding="utf-8")
    path = directory / filename
    path.write_text(markdown, encoding="utf-8")
    _logger.info("export path=%s", path)
    return path


def export_career(config: Config, storage: Storage, out_dir: Path | None = None) -> Path:
    """Portrait one-pager: the canonical profile with every claim cited inline."""
    items = [item for item in storage.list_profile_items() if item.status is ItemStatus.ACTIVE]
    conflicted = [
        item for item in storage.list_profile_items() if item.status is ItemStatus.CONFLICT
    ]
    if not items and not conflicted:
        raise IngestError(
            "the profile is empty — nothing to export. Run 'wingman ingest <resume>' first."
        )
    directory = _resolve_out_dir(config, out_dir)
    today = datetime.now(UTC).date().isoformat()
    sources = storage.count_source_records()
    parts = [
        _frontmatter("Career Profile", directory),
        '<div class="portrait">',
        "",
        "# Career Profile",
        f'<div class="meta">Generated {today} · {sources} source records · every claim cited</div>',
    ]
    sections = (
        ("Roles", ProfileItemKind.ROLE),
        ("Achievements", ProfileItemKind.ACHIEVEMENT),
        ("Testimonials", ProfileItemKind.TESTIMONIAL),
    )
    for label, kind in sections:
        section = [item for item in items if item.kind is kind]
        if not section:
            continue
        parts.append(f'<div class="section-divider"><span>{label}</span></div>')
        for item in section:
            detail = f" — {_e(item.detail)}" if item.detail else ""
            quotes = "".join(f"<blockquote>{_e(span.quote)}</blockquote>" for span in item.evidence)
            parts.append(
                '<div class="card">'
                f"<strong>{_e(item.name)}</strong>{detail}<br>"
                f'<span class="tag tag-fact">{_e(item.classification.value)}'
                f" · {item.confidence:.2f}</span>"
                f"{quotes}</div>"
            )
    skills = [item for item in items if item.kind is ProfileItemKind.SKILL]
    if skills:
        parts.append('<div class="section-divider"><span>Skills</span></div>')
        chips = "".join(f'<span class="tag tag-skill">{_e(s.name)}</span>' for s in skills)
        parts.append(f"<div>{chips}</div>")
    if conflicted:
        parts.append('<div class="section-divider"><span>Conflicts</span></div>')
        for item in conflicted:
            parts.append(
                '<div class="card"><span class="tag tag-warn">needs resolution</span> '
                f"<strong>{_e(item.name)}</strong></div>"
            )
    parts.extend(["", "</div>", ""])
    return _write(directory, "career.md", "\n".join(parts))


def export_company(
    name: str, config: Config, storage: Storage, out_dir: Path | None = None
) -> Path:
    """The company dossier, with fact/inference labels upgraded to styled tags."""
    directory = _resolve_out_dir(config, out_dir)
    report = build_company_dossier(name, config, storage)
    lines = [_frontmatter(f"Company dossier: {report.company}", directory)]
    lines.append('<div class="portrait">')
    lines.append("")
    for raw in report.markdown.splitlines():
        line = raw
        if line.startswith("- [inference] "):
            statement = _e(line.removeprefix("- [inference] "))
            line = (
                f'<div class="stance"><span class="tag tag-inference">inference</span> {statement}'
            )
        elif line.lstrip().startswith('[fact] "'):
            quote = _e(line.strip().removeprefix("[fact] "))
            line = f'<blockquote><span class="tag tag-fact">fact</span> {quote}</blockquote></div>'
        elif line.startswith("Generated: ") or line.startswith("Newest attributable document"):
            line = f'<div class="meta">{_e(line)}</div>'
        elif line.startswith("- https://"):
            body = line.removeprefix("- ")
            url, _, rest = body.partition(" ")
            line = f"- {_link(url)} {_e(rest)}" if rest else f"- {_link(url)}"
        lines.append(line)
    lines.extend(["", "</div>", ""])
    date = datetime.now(UTC).date().isoformat()
    return _write(directory, f"{_slug(report.company)}-dossier-{date}.md", "\n".join(lines))


def export_person(name: str, config: Config, storage: Storage, out_dir: Path | None = None) -> Path:
    """Landscape three-column sheet: outreach brief | point of view | related."""
    from wingman.application.people import match_people

    directory = _resolve_out_dir(config, out_dir)
    candidates = match_people(storage, name)
    if len(candidates) != 1:
        raise IngestError(
            f"no unique person matches {name!r}; see 'wingman people list'."
            if candidates
            else f"no person named {name!r}; see 'wingman people list'."
        )
    person = candidates[0]
    card = storage.get_pov_card(person.person_id)
    brief = storage.get_outreach_brief(person.person_id)
    documents = storage.list_external_documents(person.person_id)
    if card is None and brief is None and not documents:
        raise IngestError(
            f"nothing to export for {person.name} yet. Fetch their writing "
            f"('wingman people fetch \"{person.name}\"') and build a card "
            f"('wingman people pov \"{person.name}\"') first."
        )
    url_by_doc = {document.doc_id: document.url for document in documents}
    today = datetime.now(UTC).date().isoformat()
    where = " · ".join(part for part in (person.position, person.company) if part)
    meta = f"{where} · generated {today}" if where else f"generated {today}"

    left: list[str] = ["<h2>Outreach Brief</h2>"]
    if brief is not None:
        brief_meta = f"purpose: {brief.purpose.value}"
        if brief.alignment is not None:
            brief_meta += f" · alignment {brief.alignment:.3f}"
        left.append(f'<div class="meta">{_e(brief_meta)}</div>')
        for point in brief.talking_points:
            left.append(
                '<div class="stance">'
                f'<div class="their"><span class="tag tag-inference">they argue</span> '
                f"{_dimension_tag(point.dimension)}{_e(point.their_stance)}</div>"
                f'<div class="yours"><span class="tag tag-fact">you wrote</span> '
                f'“{_e(point.your_quote)}” <span class="dim">— {_e(point.corpus_doc_title)}'
                "</span></div>"
                f'<p class="point">{_e(point.point)}</p></div>'
            )
        if brief.intro_points:
            bullets = "".join(f"<li>{_e(bullet)}</li>" for bullet in brief.intro_points)
            left.append(
                '<div class="draft-panel">'
                '<div class="meta">intro material — compose it in your own voice · '
                "wingman never sends (RFC-006)</div>"
                f"<ul>{bullets}</ul></div>"
            )
    else:
        left.append(
            f'<p class="dim">No brief yet — build one with '
            f'<code>wingman people brief "{_e(person.name)}"</code>.</p>'
        )

    middle: list[str] = ["<h2>Point of View</h2>"]
    if card is not None:
        middle.append(
            f'<div class="meta">from {card.documents_used} documents · '
            f"{card.generated_at.date().isoformat()}</div>"
        )
        for stance in card.stances:
            source_url = url_by_doc.get(stance.doc_id)
            source = _link(source_url, stance.doc_title) if source_url else _e(stance.doc_title)
            via = f" via {_e(stance.organization)}" if stance.organization else ""
            middle.append(
                f'<div class="stance">{_dimension_tag(stance.dimension)}{_e(stance.statement)}'
                f"<blockquote>“{_e(stance.quote)}” "
                f'<span class="dim">— {source}{via}</span></blockquote></div>'
            )
        if card.topics:
            chips = "".join(f'<span class="tag tag-skill">{_e(t)}</span>' for t in card.topics)
            middle.append(f"<div>{chips}</div>")
    else:
        middle.append(
            f'<p class="dim">No POV card yet — build one with '
            f'<code>wingman people pov "{_e(person.name)}"</code>.</p>'
        )

    common: list[Person] = []
    if person.company:
        key = company_key(person.company)
        common = [
            other
            for other in storage.list_people()
            if other.person_id != person.person_id
            and other.origin is PersonOrigin.LINKEDIN_CONNECTIONS
            and company_key(other.company or "") == key
        ]

    right: list[str] = ["<h2>Related</h2>"]
    score, warmth_label, warmth_signals = _warmth(person, len(common))
    dots = "●" * min(score, 4) + "○" * (4 - min(score, 4))
    right.append(f'<div class="warmth warmth-{warmth_label}">{dots} {warmth_label}</div>')
    right.append(f'<p class="dim">{_e("; ".join(warmth_signals))}</p>')
    links: list[str] = []
    if person.linkedin_url:
        links.append(f"<li>{_link(person.linkedin_url, 'LinkedIn')}</li>")
    if person.email:
        links.append(f"<li>{_link(f'mailto:{person.email}', person.email)}</li>")
    if person.substack_url:
        links.append(f"<li>{_link(person.substack_url, 'Substack')}</li>")
    # The company link: any watched person's org-attributed feed for this
    # company is the best URL the workspace honestly knows for it.
    company_url = None
    if person.company:
        key = company_key(person.company)
        company_url = next(
            (
                feed.url
                for other in storage.list_people()
                for feed in other.sources
                if feed.attribution is FeedAttribution.ORGANIZATION
                and company_key(feed.org_name or "") == key
            ),
            None,
        )
    if person.company:
        company_label = f"{person.company} (company)"
        links.append(
            f"<li>{_link(company_url, company_label)}</li>"
            if company_url
            else f"<li>{_e(company_label)}</li>"
        )
    for feed in person.feeds:
        label = feed.org_name or feed.url
        links.append(
            f"<li>{_link(feed.url, label)} <span class='dim'>({feed.kind.value})</span></li>"
        )
    if links:
        right.append('<div class="meta">links</div>')
        right.append("<ul>" + "".join(links) + "</ul>")
    # "In common": this workspace knows the user's own LinkedIn connections,
    # so the honest mutual signal is: your connections at their company.
    if common and person.company:
        right.append(
            f'<div class="meta">in common — your connections at {_e(person.company)}</div>'
        )
        rows = []
        for other in sorted(common, key=lambda entry: entry.name)[:6]:
            position = f" <span class='dim'>{_e(other.position)}</span>" if other.position else ""
            rows.append(f"<li>{_e(other.name)}{position}</li>")
        more = len(common) - 6
        if more > 0:
            rows.append(f"<li><span class='dim'>+{more} more</span></li>")
        right.append("<ul>" + "".join(rows) + "</ul>")
    dated = [d.published_at for d in documents if d.published_at is not None]
    stats = f"{len(documents)} documents stored"
    if dated:
        stats += f" · newest {max(dated).date().isoformat()}"
    right.append(f'<div class="meta">writing</div><p>{_e(stats)}</p>')
    try:
        similar = similar_people(storage, name=person.name, limit=5)
    except IngestError:
        similar = None
    if similar is not None and similar.people:
        right.append('<div class="meta">thinks like</div>')
        similar_rows = "".join(
            f"<li>{_e(entry.name)} <span class='dim'>{entry.score:.3f}</span></li>"
            for entry in similar.people
        )
        right.append(f"<ul>{similar_rows}</ul>")

    markdown = "\n".join(
        [
            _frontmatter(person.name, directory, landscape=True),
            f"# {_e(person.name)}",
            f'<div class="meta">{_e(meta)}</div>',
            '<div class="sheet">',
            "<div>" + "\n".join(left) + "</div>",
            "<div>" + "\n".join(middle) + "</div>",
            "<div>" + "\n".join(right) + "</div>",
            "</div>",
            "",
        ]
    )
    return _write(directory, f"{_slug(person.name)}-{today}.md", markdown)
