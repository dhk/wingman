"""The overnight digest, rendered pretty: a styled HTML twin of the markdown.

Same design-system tokens as every export (reporting/export.py); built
from the run's structured data, not by parsing the markdown back. The
markdown stays the canonical, greppable, searchable artifact — the HTML
is the one you enjoy reading with coffee. Self-contained (inline CSS),
so it opens anywhere and attaches cleanly.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import TYPE_CHECKING

from wingman.reporting.export import WINGMAN_PDF_CSS

if TYPE_CHECKING:
    from wingman.application.focus import ActionItem, OvernightTarget

_DIGEST_CSS = """
.digest { max-width: 720px; margin: 0 auto; padding: 48px 24px 64px; }
.chips { display: flex; gap: 10px; margin: 0 0 40px; flex-wrap: wrap; }
.target-lines { list-style: none; padding-left: 0; }
.target-lines li { margin-bottom: 6px; color: var(--text-muted); }
.target-lines li.sub { padding-left: 18px; color: var(--text-dim); }
.action { border-left: 3px solid var(--accent); padding: 4px 0 4px 18px; margin: 0 0 28px; }
.action.first { border-left-color: var(--accent-orange); }
.action .what { font-weight: 600; font-size: 17px; margin: 0 0 6px; }
.action .line { margin: 0 0 4px; color: var(--text-muted); }
.action .line b { font-family: var(--font-mono); font-size: 10px; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--text-dim); margin-right: 8px; font-weight: 500; }
.action code { font-size: 12px; }
.suppressed { margin-top: 32px; }
"""


def _e(text: str) -> str:
    return html.escape(text, quote=True)


# Digest lines carry markdown links ('[title](https://…)') so long URLs stay
# readable; rendered here as anchors. Matching runs on already-escaped text.
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _rich(text: str) -> str:
    return _MD_LINK.sub(r'<a href="\2">\1</a>', _e(text))


def _chip(label: str, kind: str = "fact") -> str:
    return f'<span class="tag tag-{kind}">{_e(label)}</span>'


def _target_section(target: OvernightTarget) -> list[str]:
    mark = "✓" if target.status == "ok" else "✗"
    tone = "fact" if target.status == "ok" else "warn"
    parts = [
        '<div class="card">',
        f"<h3>{mark} {_e(target.name)} {_chip(target.kind, tone)}</h3>",
        '<ul class="target-lines">',
    ]
    for line in target.lines:
        css = ' class="sub"' if line.startswith(("  ", "new: ")) else ""
        parts.append(f"<li{css}>{_rich(line)}</li>")
    parts.extend(["</ul>", "</div>"])
    return parts


def _action_block(number: int, action: ActionItem) -> list[str]:
    first = " first" if number == 1 else ""
    parts = [
        f'<div class="action{first}">',
        f'<p class="what">{number}. {_e(action.what)}</p>',
        f'<p class="line"><b>why</b>{_e(action.why)}</p>',
        f'<p class="line"><b>who</b>{_e(action.who)}</p>',
    ]
    parts.extend(f'<p class="line"><b>evidence</b>{_rich(item)}</p>' for item in action.evidence)
    if action.key:
        parts.append(f'<p class="line"><b>key</b><code>{_e(action.key)}</code></p>')
    parts.append("</div>")
    return parts


def render_digest_html(
    now: datetime,
    targets: list[OvernightTarget],
    actions: list[ActionItem],
    suppressed: int,
    suggestions: list[str],
) -> str:
    failed = sum(1 for target in targets if target.status == "failed")
    chips = [
        _chip(f"{len(targets)} targets"),
        _chip(f"{failed} failures", "warn" if failed else "fact"),
        _chip(f"{len(actions)} actions", "inference"),
    ]
    body: list[str] = [
        f"<h1>Overnight digest — {now.date().isoformat()}</h1>",
        f'<div class="meta">generated {now.strftime("%Y-%m-%d %H:%M UTC")}</div>',
        f'<div class="chips">{"".join(chips)}</div>',
        '<div class="section-divider"><span>Action list</span></div>',
    ]
    if actions:
        for number, action in enumerate(actions, start=1):
            body.extend(_action_block(number, action))
    else:
        body.append(
            '<p class="dim">Nothing changed enough to act on — no new links, posts, or news.</p>'
        )
    if suppressed:
        body.append(
            f'<p class="dim suppressed">{suppressed} action(s) suppressed by your triage '
            "verdicts — <code>wingman actions list</code> shows them.</p>"
        )
    body.append('<div class="section-divider"><span>Targets</span></div>')
    for target in targets:
        body.extend(_target_section(target))
    if suggestions:
        body.append('<div class="section-divider"><span>Consider following</span></div>')
        body.append("<ul>")
        body.extend(f"<li>{_e(entry)}</li>" for entry in suggestions)
        body.append("</ul>")
    joined = "\n".join(body)
    return (
        "<!doctype html>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Overnight digest — {now.date().isoformat()}</title>\n"
        f"<style>\n{WINGMAN_PDF_CSS}\n{_DIGEST_CSS}</style>\n"
        f'<div class="digest">\n{joined}\n</div>\n'
    )
