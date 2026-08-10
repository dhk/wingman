"""The read surface (RFC-033): a small web UI riding the HTTP MCP server.

Conversation is for thinking; this page is for what conversation is bad
at — glancing and files. It serves what the workspace already renders
(digests, packs, dossiers, exports) behind the same capability-path
token as the MCP transport, plus one upload form for the two artifacts
that cannot travel through a chat: a LinkedIn export zip and a resume
file. Deliberately NOT here: search boxes, triage buttons, graph
browsing — the conversational client is that interface, and duplicating
it here is the road to a product this isn't (hosted tiers stay parked).

Security shape matches RFC-017: loopback bind, token in the path
(constant-time compared), reached through the user's own tunnel. File
serving is confined to the reports directory; uploads are size-capped,
land in the inbox like any other ingested artifact, and flow through
the ordinary deterministic pipelines.
"""

from __future__ import annotations

import html
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.logs import get_logger
from wingman.reporting.design_tokens import DESIGN_TOKENS_CSS

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from wingman.application.completeness import CompletenessReport
    from wingman.domain.profile import ProfileItem
    from wingman.infrastructure.tenants import TenantIndex

_logger = get_logger("webui")

# Set only by the shared multi-tenant HTTP server (RFC-048) at startup.
# None (the default) preserves today's single-workspace token-file
# compare exactly — the still-separate shape-B processes (dhk, trent)
# that stay on that model through the phased migration are unaffected.
_tenant_index: TenantIndex | None = None


def configure_tenant_index(index: TenantIndex | None) -> None:
    """Bind (or clear) the tenant index '_authorized' consults, and gate
    the self-restart panel/route on. Call once at shared-process startup,
    BEFORE 'register_ui' — 'register_ui' decides whether to mount the
    restart route at call time, so setting the index after it would leave
    the route mounted. An index set here also disables the per-tenant
    restart button (see 'register_ui'), since restarting the one process
    every tenant shares is an ops action, not a tenant self-service one.
    """
    global _tenant_index
    _tenant_index = index


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_RESUME_SUFFIXES = {".md", ".markdown", ".txt", ".pdf", ".docx", ".tex"}
_SERVE_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".pdf": "application/pdf",
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".css": "text/css",
    ".svg": "image/svg+xml",
}
_MAX_LISTED_PER_SECTION = 12
# #173: digests get their own, smaller cap — "last N overnight runs" rather
# than the generic per-section row cap, since 'wingman overnight' writes
# exactly one dated digest file per run. Files are never deleted (backup.py's
# --keep prunes disk; this only prunes what's *listed*), so older digests
# stay reachable by direct URL/search (search.py's own digest glob) even
# once they roll off this list.
_DIGEST_RUNS_LISTED = 7

# Component rules only — the token block is prepended below (issue #118 §9:
# one shared constant feeds this stylesheet and WINGMAN_PDF_CSS alike).
_UI_RULES_CSS = """
body { margin: 0; }
.ui { max-width: 720px; margin: 0 auto; padding: 24px 16px 64px;
  display: flex; flex-direction: column; gap: 24px; }
.hdr { display: flex; align-items: center; gap: 8px; padding-bottom: 12px;
  border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.hdr .dot { width: 8px; height: 8px; border-radius: 999px; background: var(--accent); }
.hdr .wordmark { font-size: 18px; font-weight: 700; color: var(--text-head); }
.hdr .meta { margin: 0; flex-basis: 100%; }
.eyebrow { font-family: var(--font-mono); font-size: 11px; letter-spacing: .04em;
  text-transform: uppercase; color: var(--text-dim); }
.hero { display: flex; align-items: center; gap: 12px; border: 1px solid var(--accent);
  background: var(--bg2); border-radius: var(--border-radius); padding: 16px;
  min-height: 64px; text-decoration: none; color: var(--text);
  transition: background .15s, border-color .15s; }
.hero:hover { background: var(--bg3); }
.hero .hbody { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.hero .title { font-size: clamp(22px, 3.4vw, 26px); font-weight: 700;
  letter-spacing: -.01em; line-height: 1.2; color: var(--text-head); }
.hero .sub { color: var(--text-muted); font-size: 14px; margin: 0; }
.hero .arrow { color: var(--accent); font-size: 20px; }
.group { display: flex; flex-direction: column; gap: 8px; }
/* Profile page (#284): a claim, what backs it, and why to look harder. */
.claim { padding: 10px 0; border-bottom: 1px solid var(--border); }
.claim-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.claim .name { font-weight: 600; }
.claim .detail { margin: 4px 0 6px; }
.claim .id { font-family: var(--font-mono); font-size: 10px; opacity: .45; margin-left: auto; }
.claim .flag { font-family: var(--font-mono); font-size: 10px; letter-spacing: .06em;
  text-transform: uppercase; padding: 1px 6px; border: 1px solid var(--border);
  border-radius: 3px; opacity: .8; }
.quote { display: flex; gap: 8px; margin: 3px 0 0 12px; font-size: 13px; opacity: .75;
  border-left: 2px solid var(--border); padding-left: 10px; }
.quote .src { font-family: var(--font-mono); font-size: 10px; opacity: .6; flex: none; }
.pair { border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; margin: 8px 0; }
.pair .claim:last-child { border-bottom: none; }
.versus { font-family: var(--font-mono); font-size: 10px; letter-spacing: .1em;
  text-transform: uppercase; opacity: .55; margin: 2px 0; }
.divider .count { font-family: var(--font-mono); font-size: 10px; opacity: .5; }
.band { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 4px 16px; margin: 12px 0 20px; }
.check { display: flex; gap: 8px; align-items: baseline; font-size: 13px; }
.check .mark { font-family: var(--font-mono); flex: none; }
.check.yes { opacity: .6; }
.check.no .mark { opacity: .9; }
.check.no { font-weight: 600; }
.divider { display: flex; align-items: center; gap: 12px; }
.divider span { font-family: var(--font-mono); font-size: 10px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--text-dim); white-space: nowrap; }
.divider::after { content: ""; flex: 1; border-top: 1px solid var(--border); }
.rows { border: 1px solid var(--border); border-radius: var(--border-radius); overflow: hidden; }
.row { display: grid; grid-template-columns: 62px 1fr auto; gap: 12px; align-items: center;
  padding: 10px 12px; min-height: 44px; text-decoration: none; color: var(--text);
  border-top: 1px solid var(--border-light); transition: background .15s; }
.row:first-child { border-top: 0; }
.row:hover { background: var(--bg3); }
.row .when { font-family: var(--font-mono); font-size: 11px; color: var(--text-dim); }
.row .arrow { color: var(--text-dim); }
.row > span:nth-child(2) { font-weight: 500; color: var(--text-head); }
.chip { font-family: var(--font-mono); font-size: 10px; text-transform: uppercase;
  letter-spacing: .04em; padding: 2px 8px; border-radius: 999px; }
.chip-digest { background: rgba(43, 80, 232, .12); color: var(--accent); }
.chip-pack { background: rgba(107, 63, 212, .12); color: var(--accent-purple); }
.chip-dossier { background: rgba(14, 149, 145, .12); color: var(--teal); }
.chip-export { background: rgba(209, 102, 10, .12); color: var(--accent-orange); }
.panel { border: 1px solid var(--border); background: var(--bg2);
  border-radius: var(--border-radius); padding: 16px;
  display: flex; flex-direction: column; gap: 16px; }
.panel p { margin: 0; color: var(--text-muted); font-size: 14px; }
.stepno { font-family: var(--font-mono); font-size: 11px; color: var(--accent);
  letter-spacing: .04em; text-transform: uppercase; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field label { font-family: var(--font-mono); font-size: 11px; text-transform: uppercase;
  letter-spacing: .04em; color: var(--text-dim); }
.field input { border: 1px solid var(--border); border-radius: var(--border-radius);
  background: var(--bg); color: var(--text); padding: 10px 12px; font: inherit; }
.field input[readonly] { color: var(--text-dim); background: var(--bg3); }
.status { display: flex; align-items: center; gap: 6px; font-family: var(--font-mono);
  font-size: 11px; color: var(--text-dim); }
.status .sdot { width: 7px; height: 7px; border-radius: 999px; background: var(--text-dim); }
.status.ok { color: var(--teal); }
.status.ok .sdot { background: var(--teal); }
.status.warn { color: var(--accent-orange); }
.status.warn .sdot { background: var(--accent-orange); }
.btn { font-family: var(--font-mono); font-size: 12px; text-transform: uppercase;
  letter-spacing: .04em; min-height: 44px; width: 100%; border: 0;
  border-radius: var(--border-radius); background: var(--accent); color: #fff;
  cursor: pointer; transition: background .15s; }
.btn:hover { background: var(--accent-hv); }
.report-box { border-left: 3px solid var(--teal); background: var(--bg2);
  border-radius: 0 var(--border-radius) var(--border-radius) 0; padding: 12px 16px;
  white-space: pre-wrap; font-family: var(--font-mono); font-size: 13px; }
.report-box.err { border-left-color: var(--accent-orange); }
.empty { border: 1px dashed var(--border); background: var(--bg2);
  border-radius: var(--border-radius); padding: 24px 16px; text-align: center;
  display: flex; flex-direction: column; gap: 4px; }
.empty b { color: var(--text-head); }
.manage-hd { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; padding: 13px 0; }
.manage-title { font-size: 15px; font-weight: 700; color: var(--text-head); }
.manage-sub { font-family: var(--font-mono); font-size: 11px; text-transform: uppercase;
  letter-spacing: .04em; color: var(--text-dim); }
.manage-body { display: flex; flex-direction: column; gap: 16px; }
.row-older { padding: 10px 12px; font-family: var(--font-mono); font-size: 11px;
  color: var(--text-dim); border-top: 1px solid var(--border-light); }
/* Desktop tabs (spec section 6): CSS-only — hidden radios + :checked siblings, no
   JavaScript. Narrow viewports never see the bar: every panel stacks in DOM
   order, so the phone page is the same content top to bottom. With CSS
   unavailable the radios and labels degrade to stacked labeled sections. */
.tabset { display: flex; flex-direction: column; gap: 24px; }
.tabset > input { position: absolute; width: 1px; height: 1px; opacity: 0;
  pointer-events: none; }
.tabbar { display: none; }
.tabpanel { display: flex; flex-direction: column; gap: 24px; }
@media (min-width: 768px) {
  .ui-setup { max-width: 560px; }
  .tabbar { display: flex; gap: 4px; border-bottom: 1px solid var(--border); }
  .tabbar label { font-family: var(--font-mono); font-size: 11px; text-transform: uppercase;
    letter-spacing: .04em; color: var(--text-dim); padding: 0 14px; min-height: 44px;
    display: flex; align-items: center; cursor: pointer; margin-bottom: -1px;
    border-bottom: 2px solid transparent; transition: color .15s, border-color .15s; }
  .tabbar label:hover { color: var(--text); }
  .tabpanel { display: none; }
  #tab-digest:checked ~ .tabpanel-digest,
  #tab-files:checked ~ .tabpanel-files,
  #tab-changelog:checked ~ .tabpanel-changelog,
  #tab-connect:checked ~ .tabpanel-connect,
  #tab-manage:checked ~ .tabpanel-manage { display: flex; }
  #tab-digest:checked ~ .tabbar label[for="tab-digest"],
  #tab-files:checked ~ .tabbar label[for="tab-files"],
  #tab-changelog:checked ~ .tabbar label[for="tab-changelog"],
  #tab-connect:checked ~ .tabbar label[for="tab-connect"],
  #tab-manage:checked ~ .tabbar label[for="tab-manage"] {
    color: var(--text); border-bottom-color: var(--accent); }
}
.changelog-rows { display: flex; flex-direction: column; }
.changelog-row { display: grid; grid-template-columns: 72px 1fr auto; gap: 12px;
  align-items: baseline; padding: 8px 0; border-top: 1px solid var(--border-light); }
.changelog-row:first-child { border-top: 0; }
.changelog-row .when { font-family: var(--font-mono); font-size: 11px; color: var(--text-dim); }
.changelog-row .pr { font-family: var(--font-mono); font-size: 11px; color: var(--text-dim); }
/* Changelog filter (User Facing / System Features / All): same hidden-radio
   + :checked-sibling technique as the outer tabset above, but its own
   smaller classes rather than reusing .tabset/.tabbar/.tabpanel -- this is
   a secondary filter inside one panel, not another layer of primary nav,
   and shouldn't carry the same visual weight. Degrades the same way too:
   below 768px filtering needs a wide-enough bar to make sense, so the
   controls hide and every row shows, same as the outer tabs' mobile stack. */
.changelog-filter > input { position: absolute; width: 1px; height: 1px; opacity: 0;
  pointer-events: none; }
.cl-filterbar { display: none; }
@media (min-width: 768px) {
  .cl-filterbar { display: flex; gap: 4px; margin-bottom: 4px; }
  .cl-filterbar label { font-family: var(--font-mono); font-size: 10px; text-transform: uppercase;
    letter-spacing: .04em; color: var(--text-dim); padding: 3px 10px; border-radius: 999px;
    border: 1px solid var(--border); cursor: pointer; transition: color .15s, border-color .15s; }
  .cl-filterbar label:hover { color: var(--text); }
  #cl-uf:checked ~ .cl-filterbar label[for="cl-uf"],
  #cl-sys:checked ~ .cl-filterbar label[for="cl-sys"],
  #cl-all:checked ~ .cl-filterbar label[for="cl-all"] {
    color: var(--text); border-color: var(--accent); }
  #cl-uf:checked ~ .changelog-rows .cl-sys,
  #cl-sys:checked ~ .changelog-rows .cl-uf { display: none; }
}
"""

_UI_CSS = DESIGN_TOKENS_CSS + _UI_RULES_CSS


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _page(title: str, body: str, shell: str = "ui") -> HTMLResponse:
    # Tokens ride _UI_CSS (shared block, emitted once); the export stylesheet
    # contributes only its component rules so nothing is defined twice.
    from wingman.reporting.export import WINGMAN_PDF_RULES_CSS

    return HTMLResponse(
        "<!doctype html>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(title)}</title>\n"
        f"<style>\n{_UI_CSS}\n{WINGMAN_PDF_RULES_CSS}</style>\n"
        f'<div class="{shell}">\n{body}\n</div>\n'
    )


def _authorized(request: Request) -> Config | None:
    """The path token is the credential (RFC-017 shape); wrong or absent -> None.

    Under a shared multi-tenant process ('configure_tenant_index' called),
    the token identifies WHICH tenant, not just whether one is authorized —
    resolved via the tenant index's hash lookup (no per-tenant compare
    loop needed, and no request-scoped context var either: this function
    already receives the token directly and returns that tenant's Config
    straight to its caller). Single-tenant mode (the default, no index
    configured) is the exact single-file 'compare_digest' check unchanged.
    """
    presented = str(request.path_params.get("token", ""))
    if _tenant_index is not None:
        tenant = _tenant_index.resolve(presented)
        return tenant.config() if tenant is not None else None
    config = load_config()
    token_path = config.data_dir / "mcp-http-token"
    if not token_path.exists():
        return None
    expected = token_path.read_text(encoding="utf-8").strip()
    if not secrets.compare_digest(presented, expected):
        return None
    return config


def _not_found() -> Response:
    # Wrong token and wrong path look identical: no oracle for token guessing.
    return Response("not found", status_code=404)


def _listed_files(root: Path) -> list[tuple[str, Path]]:
    """(section, file) pairs worth linking, newest first within each section."""
    if not root.exists():
        return []
    from wingman.reporting.export import STYLESHEET_NAME

    sections: list[tuple[str, Path]] = []
    candidates = [
        path
        for path in root.rglob("*")
        if path.suffix in _SERVE_TYPES and path.name != STYLESHEET_NAME
    ]
    # A digest's .md twin duplicates its .html, and a report's .json sidecar
    # duplicates its .md write-up (career.md/.json, fit-brief-*.md/.json):
    # prefer the human-readable one in both cases.
    skip = {path.with_suffix(".md") for path in candidates if path.suffix == ".html"}
    skip |= {path.with_suffix(".json") for path in candidates if path.suffix == ".md"}
    for path in candidates:
        if path in skip or path.name in ("latest.md", "latest.html"):
            continue
        section = path.parent.relative_to(root).as_posix()
        sections.append(("reports" if section == "." else section, path))
    sections.sort(key=lambda entry: entry[1].stat().st_mtime, reverse=True)
    return sections


async def ui_home_redirect(request: Request) -> Response:
    """Canonicalize to the trailing-slash landing with a RELATIVE Location.

    Every URL this UI emits is relative, so it works unchanged behind a
    path-mounting reverse proxy (e.g. 'tailscale serve --set-path /trent'),
    which strips the mount prefix before forwarding: the server never needs
    to know its public prefix. That only holds if the landing URL ends in
    '/', and only a relative redirect ('<token>/', resolved by the browser
    against the public URL) preserves the prefix the server can't see.
    """
    if _authorized(request) is None:
        return _not_found()
    return RedirectResponse(url=f"{request.path_params['token']}/", status_code=307)


# Directory -> humanized group name (issue #95: never raw directory names).
_GROUP_NAMES = {
    "digests": "Overnight digests",
    "packs": "Application packs",
    "dossiers": "Company dossiers",
    "pdf": "Exports",
    "reports": "Reports",
    "charts": "Value charts",
}
_STAMP = re.compile(r"(20\d{6})T(\d{2})(\d{2})\d*Z?")
_ISO_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_FRONTMATTER_TITLE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)


def _frontmatter_title(path: Path) -> str | None:
    """A `title:` field from YAML frontmatter, when the file carries one.

    reporting/export.py's `_frontmatter()` (and pack.py's own frontmatter
    block) already write a real, human-chosen title for every export —
    prefer it over guessing one back from the filename.
    """
    if path.suffix != ".md":
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    match = _FRONTMATTER_TITLE.search(text[:end])
    if match is None:
        return None
    title = match.group(1).strip().strip("\"'")
    return title or None


def _humanize(path: Path, now: datetime) -> tuple[str, str]:
    """(date label, title) for an artifact — filenames never reach the page."""
    stem = path.stem
    label = ""
    stamped = _STAMP.search(stem)
    if stamped:
        when = datetime.strptime(stamped.group(1), "%Y%m%d").replace(tzinfo=UTC)
        hm = f"{stamped.group(2)}:{stamped.group(3)}"
        label = f"Today · {hm}" if when.date() == now.date() else when.strftime("%d %b")
        stem = _STAMP.sub("", stem)
    else:
        dated = _ISO_DATE.search(stem)
        if dated:
            when = datetime.strptime(dated.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
            label = "Today" if when.date() == now.date() else when.strftime("%d %b")
            stem = _ISO_DATE.sub("", stem)
    title = _frontmatter_title(path)
    if title is None:
        words = [w for w in re.split(r"[-_.]+", stem) if w]
        if words and words[0].lower() == "overnight":
            title = "Overnight digest"
        elif words and words[0].lower() == "pack":
            rest = " ".join(w.capitalize() for w in words[1:])
            title = rest or "Application pack"
        else:
            title = " ".join(w.capitalize() for w in words) or path.name
    return label, title


def _group_name(section: str) -> str:
    return _GROUP_NAMES.get(section, section.replace("-", " ").replace("_", " ").title())


def _header(config: Config, show_path: bool = True) -> str:
    # show_path=False for anything that leaves the server. The workspace
    # path names the host account ('/home/trent/.local/share/wingman'),
    # which is a disclosure in a file that gets saved to a laptop and
    # sometimes forwarded — the same argument the token check already makes.
    meta = f'<div class="meta">{_e(str(config.data_dir))}</div>' if show_path else ""
    return (
        '<div class="hdr"><span class="dot"></span>'
        f'<span class="wordmark">Wingman</span>{meta}</div>'
    )


def _section_cap(section: str) -> int:
    return _DIGEST_RUNS_LISTED if section == "digests" else _MAX_LISTED_PER_SECTION


def _artifact_sections(config: Config, now: datetime) -> list[tuple[str, str]]:
    """(section, rendered group) pairs \u2014 newest first, capped per '_section_cap'."""
    grouped: dict[str, list[Path]] = {}
    for section, path in _listed_files(config.reports_dir):
        grouped.setdefault(section, []).append(path)
    parts: list[tuple[str, str]] = []
    for section in sorted(grouped):
        cap = _section_cap(section)
        rows = []
        for path in grouped[section][:cap]:
            rel = path.relative_to(config.reports_dir).as_posix()
            label, title = _humanize(path, now)
            rows.append(
                f'<a class="row" href="file/{_e(rel)}"><span class="when">{_e(label)}</span>'
                f"<span>{_e(title)}</span>" + '<span class="arrow">\u2192</span></a>'
            )
        if len(grouped[section]) > cap:
            rows.append('<div class="row-older">older\u2026</div>')
        parts.append(
            (
                section,
                f'<div class="group"><div class="divider"><span>{_e(_group_name(section))}</span>'
                '</div><div class="rows">' + "".join(rows) + "</div></div>",
            )
        )
    return parts


def _changelog_date_label(iso_date: str, now: datetime) -> str:
    when = datetime.strptime(iso_date, "%Y-%m-%d").replace(tzinfo=UTC)
    return "Today" if when.date() == now.date() else when.strftime("%d %b")


def _changelog_tab_label(today_count: int, week_count: int) -> str:
    return f"Changelog ({today_count} new today / {week_count} last 7 days)"


def _changelog_panel(now: datetime) -> tuple[str, str]:
    """(tab label with counts, rendered panel) — issue #145; the User Facing
    / System Features / All filter added on direct request.

    Entries are wingman's own curated merged-PR history (RFC-038), not
    workspace data, so this needs no Config and renders the same for every
    instance running this build. The tab label's own "N new today / M last
    7 days" count always reflects user-facing entries only, matching #145's
    original intent, regardless of which filter the panel itself defaults
    to or the viewer later picks.

    User Facing / System Features / All is a single combined, newest-first
    list (both categories interleaved by date, each row tagged) filtered
    client-side via CSS — not three separately-fetched and separately-capped
    lists. That keeps "All" in correct chronological order for free, at the
    cost of a filtered sub-view sometimes showing fewer than
    DEFAULT_DISPLAY_LIMIT rows if the newest DEFAULT_DISPLAY_LIMIT entries
    happen to skew toward the other category — acceptable for "what's
    happened lately", not worth a second display cap to avoid.
    """
    from wingman.domain.changelog import (
        DEFAULT_DISPLAY_LIMIT,
        counts_today_and_week,
        is_user_facing,
        load_entries,
        user_facing_entries,
    )

    today_count, week_count = counts_today_and_week(user_facing_entries(), now.date())
    all_entries = sorted(load_entries(), key=lambda entry: entry.date, reverse=True)
    rows = "".join(
        f'<div class="changelog-row {"cl-uf" if is_user_facing(entry.title) else "cl-sys"}">'
        f'<span class="when">{_e(_changelog_date_label(entry.date, now))}</span>'
        f"<span>{_e(entry.title)}</span>"
        f'<span class="pr">#{entry.pr}</span></div>'
        for entry in all_entries[:DEFAULT_DISPLAY_LIMIT]
    )
    if len(all_entries) > DEFAULT_DISPLAY_LIMIT:
        rows += '<div class="row-older">older…</div>'
    if not rows:
        return _changelog_tab_label(today_count, week_count), ""
    panel = (
        '<div class="changelog-filter">'
        '<input type="radio" name="cl-filter" id="cl-uf" checked>'
        '<input type="radio" name="cl-filter" id="cl-sys">'
        '<input type="radio" name="cl-filter" id="cl-all">'
        '<nav class="cl-filterbar">'
        '<label for="cl-uf">User Facing</label>'
        '<label for="cl-sys">System Features</label>'
        '<label for="cl-all">All</label>'
        "</nav>"
        f'<div class="changelog-rows">{rows}</div>'
        "</div>"
    )
    return _changelog_tab_label(today_count, week_count), panel


def _tabset(panels: list[tuple[str, str, str]], selected: str | None = None) -> str:
    """Desktop tabs (spec section 6): radios first, then labels, then panels.

    CSS-only \u2014 hidden radio inputs drive :checked sibling selectors; the page
    carries no JavaScript. Below 768px the bar is hidden and every panel
    stacks in DOM order, so narrow viewports read the same single column as
    before; with CSS unavailable the whole thing degrades to stacked labeled
    sections. Empty panels are dropped (no dead tab), and a lone panel needs
    no chrome at all.

    `selected` (a ?tab= query param, typically) picks which radio starts
    checked -- how a report page's nav bar jumps back to a specific tab
    instead of always landing on the first one. An unknown or absent value
    falls back to the first panel, same as before that param existed.
    """
    filled = [(key, label, content) for key, label, content in panels if content]
    if not filled:
        return ""
    if len(filled) == 1:
        return filled[0][2]
    default_key = selected if selected in {key for key, _, _ in filled} else filled[0][0]
    inputs = "".join(
        f'<input type="radio" name="view" id="tab-{key}"{" checked" if key == default_key else ""}>'
        for key, _, _ in filled
    )
    labels = "".join(f'<label for="tab-{key}">{_e(label)}</label>' for key, label, _ in filled)
    sections = "\n".join(
        f'<div class="tabpanel tabpanel-{key}">\n{content}\n</div>' for key, _, content in filled
    )
    return f'<div class="tabset">\n{inputs}\n<nav class="tabbar">{labels}</nav>\n{sections}\n</div>'


def _tiers(
    groups: list[tuple[str, str]],
    manage: str,
    connect: str,
    now: datetime,
    selected: str | None = None,
) -> str:
    """Digest · Files · Changelog · Connect · Manage (tabs at >=768px, a stack below)."""
    digest = "\n".join(html for section, html in groups if section.split("/")[0] == "digests")
    files = "\n".join(html for section, html in groups if section.split("/")[0] != "digests")
    changelog_label, changelog = _changelog_panel(now)
    return _tabset(
        [
            ("digest", "Digest", digest),
            ("files", "Files", files),
            ("changelog", changelog_label, changelog),
            ("connect", "Connect", connect),
            ("manage", "Manage", manage),
        ],
        selected=selected,
    )


def _upload_panel(step: str = "") -> str:
    lead = f'<span class="stepno">{_e(step)}</span>' if step else ""
    return (
        f'<div class="panel">{lead}'
        "<p>A LinkedIn data-export <b>.zip</b>, or a resume "
        "(<b>.md .txt .pdf .docx .tex</b>). It lands in the inbox and runs the "
        "ordinary ingest pipeline — nothing else is touched.</p>"
        '<form method="post" enctype="multipart/form-data" action="upload" class="field">'
        '<label>File</label><input type="file" name="file" required>'
        '<button class="btn">Upload &amp; ingest</button></form></div>'
    )


def _own_prefix(request: Request, token: str) -> str:
    """The mount prefix this request actually arrived under ('' or '/trent').

    Read back from the matched path rather than threaded through as state,
    so it is always the prefix this instance is really mounted at — the
    same value 'register_ui' was given, computed with zero extra plumbing.
    """
    suffix = f"/ui/{token}/"
    path = request.url.path
    return path[: -len(suffix)] if path.endswith(suffix) else ""


def _url_field(label: str, value: str) -> str:
    return (
        f'<div class="field"><label>{_e(label)}</label>'
        f'<input type="text" value="{_e(value)}" readonly></div>'
    )


def _connect_panel(request: Request, token: str, step: str = "") -> str:
    """Same URLs 'wingman mcp url' prints, rendered here so reaching them
    never requires shell access to the host — the point for a friend
    running someone else's box (MULTI-INSTANCE-DESIGN.md shape B).
    """
    from wingman.mcp_server import (
        _extra_allowed_hosts,
        _tunnel_port,
        connector_urls,
    )
    from wingman.mcp_server import (
        server as mcp_server,
    )

    lead = (
        f'<span class="stepno">{_e(step)}</span>'
        if step
        else '<span class="stepno">Connect a client</span>'
    )
    host = getattr(mcp_server.settings, "host", "127.0.0.1")
    port = getattr(mcp_server.settings, "port", 8787)
    prefix = _own_prefix(request, token)
    extra_hosts = _extra_allowed_hosts(None)
    fields = "".join(
        _url_field(label, url)
        for label, url in connector_urls(
            token, extra_hosts, host=host, port=port, prefix=prefix, tunnel_port=_tunnel_port()
        )
    )
    hint = ""
    if not extra_hosts:
        hint = (
            '<p class="dim">No tunnel hostname detected — only reachable from this '
            "machine right now. Run <code>tailscale serve</code> (your own devices) or "
            "<code>tailscale funnel</code> (claude.ai web/mobile) to reach it elsewhere.</p>"
        )
    return (
        f'<div class="panel">{lead}'
        "<p>Paste the MCP connector URL into claude.ai → Settings → Connectors "
        "→ Add custom connector — the same URL works in Claude Desktop and the "
        "mobile apps. Treat both URLs like a password: anyone holding one can read and "
        "upload to this workspace.</p>"
        f"{hint}{fields}</div>"
    )


def _key_field(short: str, env_var: str, source: str) -> str:
    label = f"{short.capitalize()} key"
    if source == "environment":
        return (
            f'<div class="field"><label>{_e(label)}</label>'
            '<input type="password" value="********" readonly>'
            f'<span class="status warn"><span class="sdot"></span>'
            f"set in the service environment \u2014 your own key would take precedence"
            "</span></div>"
        )
    if source == "workspace file":
        status = (
            '<span class="status ok"><span class="sdot"></span>verified · workspace file</span>'
        )
    else:
        status = '<span class="status"><span class="sdot"></span>not set</span>'
    return (
        f'<div class="field"><label>{_e(label)}</label>'
        f'<input type="password" name="{_e(short)}" autocomplete="off">'
        f"{status}</div>"
    )


def _restart_panel() -> str:
    """Self-restart (#133): a single-click POST, same trust level as the
    key form and upload button above it — no extra JS confirm dialog
    (this page is deliberately JS-free), matched by the fact that a
    restart is brief downtime, not data loss.

    Withheld entirely under a shared multi-tenant process ('_tenant_index'
    set, RFC-048): RFC-041's premise for trusting this button was that
    restarting only ever affects the token-holder's OWN process, inside
    their own Unix account boundary. Under one shared process there is no
    such boundary — any tenant's own token, used exactly as designed
    here, would restart every other tenant's in-flight session too. A
    shared-process restart is an ops action, not a tenant self-service one.
    """
    if _tenant_index is not None:
        return (
            '<div class="panel"><span class="stepno">Restart</span>'
            "<p>This workspace runs on a shared server alongside other tenants — "
            "restarting it here would also restart theirs, so that action isn't "
            "offered from this panel. Restarting the shared process is an "
            "operator action.</p></div>"
        )
    from wingman.infrastructure.self_restart import systemd_manages_this_instance

    if not systemd_manages_this_instance():
        return (
            '<div class="panel"><span class="stepno">Restart</span>'
            "<p>This instance wasn't started via systemd, so it has no supervisor to "
            "bring it back up after a self-restart. Restart it from the host instead: "
            "<code>wingman mcp stop &amp;&amp; wingman-mcp --http</code> "
            "(or <code>wingman-ctl start</code>).</p></div>"
        )
    return (
        '<div class="panel"><span class="stepno">Restart</span>'
        "<p>Picks up code or config changes that were already deployed but not yet "
        "loaded. The page will be briefly unreachable.</p>"
        '<form method="post" action="restart"><button class="btn">Restart server</button>'
        "</form></div>"
    )


def _keys_panel(config: Config, step: str = "") -> str:
    lead = (
        f'<span class="stepno">{_e(step)}</span>'
        if step
        else '<span class="stepno">API keys</span>'
    )
    fields = "".join(
        _key_field(short, env_var, source)
        for short, env_var, source in key_status_rows(config.data_dir)
    )
    return (
        f'<div class="panel">{lead}'
        "<p>Each key is <b>verified against its provider</b> before it is stored "
        "(workspace file, owner-only). Your own key is the one that gets used \u2014 "
        "anything set in the service environment is only a fallback.</p>"
        f'<form method="post" action="keys" class="field">{fields}'
        '<button class="btn">Verify &amp; store</button></form></div>'
    )


async def ui_home(request: Request) -> Response:
    config = _authorized(request)
    if config is None:
        return _not_found()
    now = datetime.now(UTC)
    has_workspace = config.db_path.exists()
    latest = config.reports_dir / "digests" / "latest.html"
    token = str(request.path_params["token"])
    selected_tab = request.query_params.get("tab")
    body: list[str] = [_header(config)]

    if not has_workspace:
        # State 2 — fresh: guided setup, nothing dead above the fold.
        body.append("<h1>Set up your workspace</h1>")
        body.append(
            '<p class="dim">Two steps and Wingman is yours: a key it can spend, '
            "and your data to reason over.</p>"
        )
        body.append(_keys_panel(config, step="01 \u2014 Add an API key"))
        body.append(_upload_panel(step="02 \u2014 Upload your data"))
        body.append(_connect_panel(request, token, step="03 \u2014 Connect a Claude client"))
        # No tabs here (nothing to tab yet) \u2014 desktop just centers the setup.
        return _page("Wingman \u2014 setup", "\n".join(body), shell="ui ui-setup")

    if latest.exists():
        # State 1 — daily: the digest is the fold.
        stamp = datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC)
        eyebrow = (
            f"Today · {stamp.strftime('%H:%M')} UTC"
            if stamp.date() == now.date()
            else stamp.strftime("%d %b · %H:%M UTC")
        )
        body.append(
            '<a class="hero" href="file/digests/latest.html"><div class="hbody">'
            f'<span class="eyebrow">{_e(eyebrow)}</span>'
            '<span class="title">Overnight digest</span>'
            '<p class="sub">Actions first, then everything that changed.</p>'
            "</div>" + '<span class="arrow">\u2192</span></a>'
        )
        manage = (
            '<div class="manage"><div class="manage-hd"><span class="manage-title">Manage</span><span class="manage-sub">\u2014 keys &amp; uploads</span></div>'
            '<div class="manage-body">'
            + _upload_panel()
            + _keys_panel(config)
            + _restart_panel()
            + "</div></div>"
        )
        body.append(
            _tiers(
                _artifact_sections(config, now),
                manage,
                _connect_panel(request, token),
                now,
                selected=selected_tab,
            )
        )
        return _page("Wingman", "\n".join(body))

    # State 3 — degraded: workspace lives, no digest yet.
    body.append(
        '<div class="empty"><span class="eyebrow" style="color: var(--accent-orange)">'
        "No digest yet</span><b>The first overnight run writes one.</b>"
        '<span class="dim">Run <code>wingman overnight</code> \u2014 or follow a company first.</span></div>'
    )
    manage = (
        '<div class="manage"><div class="manage-hd"><span class="manage-title">Manage</span><span class="manage-sub">\u2014 keys &amp; uploads</span></div>'
        '<div class="manage-body">'
        + _upload_panel()
        + _keys_panel(config)
        + _restart_panel()
        + "</div></div>"
    )
    body.append(
        _tiers(
            _artifact_sections(config, now),
            manage,
            _connect_panel(request, token),
            now,
            selected=selected_tab,
        )
    )
    return _page("Wingman", "\n".join(body))


_REPORT_NAV_TABS = (
    ("digest", "Digest"),
    ("files", "Files"),
    ("changelog", "Changelog"),
    ("connect", "Connect"),
    ("manage", "Manage"),
)
_REPORT_NAV_CSS = """
.wg-nav { display: flex; gap: 4px; padding: 8px 16px; margin-bottom: 8px;
  border-bottom: 1px solid var(--border); font-family: var(--font-mono); font-size: 11px;
  text-transform: uppercase; letter-spacing: .04em; }
.wg-nav a { color: var(--text-dim); text-decoration: none; padding: 6px 10px;
  border-radius: var(--border-radius); }
.wg-nav a:hover { color: var(--text); background: var(--bg2); }
@media print { .wg-nav { display: none; } }
"""


def _inject_report_nav(html_text: str, rel_path: str) -> str:
    """A live 'back to Wingman' bar, added only at serve time — never baked into
    the generated file itself, so the artifact stays the same portable,
    self-contained document whether opened through the web UI, downloaded, or
    read straight off disk. `rel_path` is the file's path under reports/ (the
    'path' route param), used to compute how many levels back to the tabbed
    home page. A page missing the expected '</style>' shell is left untouched
    rather than guessing where to inject.
    """
    marker = "</style>\n"
    if marker not in html_text:
        return html_text
    up = "../" * (rel_path.count("/") + 1)
    links = "".join(f'<a href="{up}?tab={key}">{_e(label)}</a>' for key, label in _REPORT_NAV_TABS)
    nav = f'<nav class="wg-nav">{links}</nav>\n'
    return html_text.replace(marker, f"{_REPORT_NAV_CSS}{marker}{nav}", 1)


async def ui_file(request: Request) -> Response:
    config = _authorized(request)
    if config is None:
        return _not_found()
    root = config.reports_dir.resolve()
    rel_path = str(request.path_params["path"])
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return _not_found()
    media = _SERVE_TYPES.get(target.suffix)
    if media is None:
        return _not_found()
    if target.suffix == ".html":
        content = _inject_report_nav(target.read_text(encoding="utf-8"), rel_path)
        return HTMLResponse(content)
    return FileResponse(target, media_type=media)


async def ui_upload(request: Request) -> Response:
    config = _authorized(request)
    if config is None:
        return _not_found()
    back = '<p><a href="./">&larr; back</a></p>'

    form = await request.form()
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        return _page("Upload", f'{back}<div class="report-box err">No file was sent.</div>')
    data = await upload.read()
    name = Path(upload.filename or "upload").name
    suffix = Path(name).suffix.lower()
    if len(data) > MAX_UPLOAD_BYTES:
        return _page(
            "Upload",
            f'{back}<div class="report-box err">{_e(name)} is over the '
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.</div>",
        )
    if suffix != ".zip" and suffix not in _RESUME_SUFFIXES:
        return _page(
            "Upload",
            f'{back}<div class="report-box err">Unsupported type {_e(suffix or name)}: '
            "send a LinkedIn export .zip or a resume (.md .txt .pdf .docx .tex).</div>",
        )
    # A fresh workspace initializes on first upload (issue #95: the setup
    # state must never present a dead control; Storage creates the schema).
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    stored = config.inbox_dir / f"{stamp}-{name}"
    config.inbox_dir.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(data)
    _logger.info("webui upload name=%s bytes=%d", name, len(data))

    from wingman.application.ingest import IngestError, ingest_resume
    from wingman.application.linkedin import import_linkedin
    from wingman.infrastructure.storage import Storage
    from wingman.providers.base import CapabilityClass, ProviderError
    from wingman.providers.router import ModelConfigError, get_provider

    try:
        with Storage(config.db_path) as storage:
            if suffix == ".zip":
                report = import_linkedin(stored, config, storage)
                summary = (
                    f"LinkedIn export imported: {report.positions} positions, "
                    f"{report.skills} skills, {report.recommendations} recommendations.\n"
                    f"Accepted: {report.counts.accepted}  Updated: {report.counts.updated}  "
                    f"Retired: {report.counts.retired}  Conflicts: {report.counts.conflicts}"
                )
            else:
                ingested = ingest_resume(
                    stored, config, storage, get_provider(CapabilityClass.EXTRACT_FAST, config)
                )
                rejected = "".join(
                    f"\n  rejected {item.name!r}: {item.reason}" for item in ingested.rejected
                )
                summary = (
                    f"Resume ingested. Accepted: {ingested.accepted}  "
                    f"Updated: {ingested.updated}  Retired: {ingested.retired}  "
                    f"Conflicts: {ingested.conflicts}  "
                    f"Rejected: {len(ingested.rejected)}{rejected}"
                )
    except (IngestError, ModelConfigError, ProviderError) as exc:
        return _page(
            "Upload", f'{back}<div class="report-box err">Ingestion failed: {_e(str(exc))}</div>'
        )
    return _page("Upload", f'{back}<div class="report-box">{_e(summary)}</div>')


def key_status_rows(data_dir: Path) -> list[tuple[str, str, str]]:
    """(short, env var, source) per key: environment / workspace file / not set."""
    import os

    from wingman.infrastructure.keys import KNOWN_KEYS, read_workspace_keys

    stored = read_workspace_keys(data_dir)
    rows: list[tuple[str, str, str]] = []
    for short, env_var in KNOWN_KEYS.items():
        if os.environ.get(env_var, "").strip():
            source = "environment"
        elif env_var in stored:
            source = "workspace file"
        else:
            source = "not set"
        rows.append((short, env_var, source))
    return rows


def _validate_anthropic(value: str) -> str | None:
    """A free, authenticated call: models.list succeeds only for a live key."""
    import anthropic

    try:
        anthropic.Anthropic(api_key=value).models.list()
    except anthropic.AuthenticationError:
        return "Anthropic rejected the key (authentication failed)."
    except Exception as exc:  # noqa: BLE001 — network shape varies; report, don't store
        return f"could not verify the Anthropic key ({exc})."
    return None


def _validate_voyage(value: str) -> str | None:
    """One tiny embed against the real endpoint; 401/403 means a bad key."""
    import httpx

    try:
        response = httpx.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {value}"},
            json={"input": ["ok"], "model": "voyage-3-lite"},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return f"could not verify the Voyage key ({exc})."
    if response.status_code in (401, 403):
        return "Voyage rejected the key (authentication failed)."
    if response.status_code >= 400:
        return f"Voyage verification failed (HTTP {response.status_code})."
    return None


# Injectable for tests: short name -> validator(value) -> error | None.
VALIDATORS = {"anthropic": _validate_anthropic, "voyage": _validate_voyage}


async def ui_keys(request: Request) -> Response:
    config = _authorized(request)
    if config is None:
        return _not_found()
    back = '<p><a href="./">&larr; back</a></p>'
    from wingman.infrastructure.keys import KeyStoreError, store_workspace_key

    form = await request.form()
    lines: list[str] = []
    failures: list[str] = []
    for short in ("anthropic", "voyage"):
        raw = form.get(short)
        value = raw.strip() if isinstance(raw, str) else ""
        if not value:
            continue
        error = VALIDATORS[short](value)
        if error is not None:
            failures.append(f"{short}: {error} Nothing was stored for it.")
            continue
        try:
            live = store_workspace_key(config.data_dir, short, value)
        except KeyStoreError as exc:
            failures.append(f"{short}: {exc}")
            continue
        state = (
            "verified and live now"
            if live
            else "verified and stored — it takes effect on the next call"
        )
        lines.append(f"{short}: {state}.")
        _logger.info("webui key stored short=%s live=%s", short, live)  # never the value
    if not lines and not failures:
        return _page("Keys", f'{back}<div class="report-box err">No key was entered.</div>')
    blocks = ""
    if lines:
        blocks += f'<div class="report-box">{_e(chr(10).join(lines))}</div>'
    if failures:
        blocks += f'<div class="report-box err">{_e(chr(10).join(failures))}</div>'
    return _page("Keys", back + blocks)


async def ui_restart(request: Request) -> Response:
    """Self-restart (#133): the token that authorizes this request is this
    instance's own — never reachable with any other instance's token, and
    never surfaced anywhere but this instance's own Manage panel.

    Refuses outright under a shared multi-tenant process (RFC-048), as a
    second, independent layer of defense alongside 'register_ui' not
    mounting this route at all in that mode — belt and suspenders, since
    a mistakenly-reachable restart here would take down every tenant's
    session, not just the token-holder's own.
    """
    if _tenant_index is not None:
        return _not_found()
    if _authorized(request) is None:
        return _not_found()
    from wingman.infrastructure.self_restart import systemd_manages_this_instance, trigger_restart

    back = '<p><a href="./">&larr; back</a></p>'
    if not systemd_manages_this_instance():
        return _page(
            "Restart",
            f'{back}<div class="report-box err">Not systemd-managed — restart it from the '
            "host: <code>wingman mcp stop &amp;&amp; wingman-mcp --http</code> "
            "(or <code>wingman-ctl start</code>).</div>",
        )
    _logger.info("webui restart triggered")
    # Fire-and-forget: 'trigger_restart' only launches 'systemctl restart'
    # (a few ms to spawn) and returns immediately, well before systemd's
    # own restart transaction gets far enough to signal this process — the
    # response below still gets built and handed to the ASGI server first.
    trigger_restart()
    return _page(
        "Restart",
        '<div class="report-box">Restarting now — this page will be unreachable for a few '
        'seconds.</div><meta http-equiv="refresh" content="5;url=./">'
        '<p><a href="./">&larr; back now</a></p>',
    )


_registered_prefixes: set[str] = set()

# Captured once at import time (~= process start): the admin installations
# page (#130) polls this to show "running since" without needing any
# cross-workspace filesystem access into another instance's own state.
_STARTED_AT = datetime.now(UTC).isoformat(timespec="seconds")


async def ui_health(request: Request) -> Response:
    """Deliberately unauthenticated: version + start time only, nothing
    workspace-specific (no owner, no path, no data) — loopback-bound like
    the rest of this server, so the exposure is the same as 'ps' already
    gives anyone on the box. Lets the admin installations page (#130) tell
    an instance is up without holding its capability token.
    """
    from wingman.version import wingman_version

    return JSONResponse({"version": wingman_version(), "started_at": _STARTED_AT})


# --- profile page (#284) ----------------------------------------------
#
# career.md is reachable here as a rendered file, but it is flat: it
# cannot show which claims are contested, which are inference rather than
# fact, or what an ingest just did. One re-ingest reported "Retired: 23"
# and the only way to learn WHICH was to ask. RFC-028 records that
# lineage; nothing surfaced it.
#
# Read-only by design. Actions (resolve/rm/rekind/rename) mirror
# profile_manage and want their own slice, partly because write endpoints
# under the shared multi-tenant process need the gating RFC-041
# established when it removed the restart button.


def _claim_flags(item: ProfileItem) -> list[str]:
    """Why a reader should look harder at this claim."""
    flags: list[str] = []
    if item.classification.value != "fact":
        flags.append(item.classification.value)
    if item.confidence < 0.9:
        flags.append(f"confidence {item.confidence:.2f}")
    if len(item.evidence) == 1:
        flags.append("one quote")
    return flags


def _evidence_html(item: ProfileItem) -> str:
    rows = []
    for span in item.evidence:
        rows.append(
            f'<div class="quote"><span class="src">{_e(span.source_record_id[:8])}</span>'
            f"<span>{_e(span.quote)}</span></div>"
        )
    return "".join(rows)


def _profile_item_html(item: ProfileItem, tenure: str = "") -> str:
    detail = f'<div class="detail">{_e(item.detail)}</div>' if item.detail else ""
    flags = "".join(f'<span class="flag">{_e(f)}</span>' for f in _claim_flags(item))
    return (
        f'<div class="claim"><div class="claim-head"><span class="name">{_e(item.name)}</span>'
        f'<span class="dim">{_e(tenure)}</span>{flags}'
        f'<span class="id">{_e(item.item_id[:8])}</span></div>'
        f"{detail}{_evidence_html(item)}</div>"
    )


def _plural(count: int, noun: str) -> str:
    """'1 role', '2 roles' — six user-visible strings sat on the most
    common first-ingest state (one role, one skill) before this."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _completeness_band(report: CompletenessReport, items: list[ProfileItem]) -> str:
    """What is filled in, and what the gap costs — profile-scoped.

    The counts come from application/completeness.py (#313), not from a
    second walk of the same data — one measurement, one answer, no drift.
    This function's own job is the part that report deliberately leaves to
    its callers: saying what each gap COSTS. 'Roles: 0' is a number;
    "nothing here shows tenure, title or seniority" is the reason to care.

    The two claim-quality rows (contested, single-quote) stay local: they
    are properties of this page's own subject matter, and the report is
    scoped to section counts across the whole workspace.
    """
    from wingman.domain.profile import ItemStatus, ProfileItemKind

    reported = (
        ProfileItemKind.ROLE,
        ProfileItemKind.ACHIEVEMENT,
        ProfileItemKind.SKILL,
        ProfileItemKind.TESTIMONIAL,
    )
    counts = {
        ProfileItemKind.ROLE: report.career.roles,
        ProfileItemKind.ACHIEVEMENT: report.career.achievements,
        ProfileItemKind.SKILL: report.career.skills,
        ProfileItemKind.TESTIMONIAL: report.career.testimonials,
    }
    active = [i for i in items if i.status is ItemStatus.ACTIVE]
    contested = sum(1 for i in items if i.status is ItemStatus.CONFLICT)
    # Only the kinds this band actually reports. INTERVIEW nominations are
    # built with exactly one evidence span by construction
    # (application/interview.py) and nothing ever adds a second, so counting
    # them meant "N claims rest on a single quote" could never go green for
    # anyone who used the interview — and the number named items that appear
    # in none of the page's sections, so it could not be reconciled.
    thin = sum(1 for i in active if i.kind in reported and len(i.evidence) == 1)
    criteria_set = report.job_criteria.exists

    rows: list[tuple[bool, str]] = [
        (
            counts[ProfileItemKind.ROLE] > 0,
            _plural(counts[ProfileItemKind.ROLE], "role")
            if counts[ProfileItemKind.ROLE]
            else "No roles — nothing here shows tenure, title or seniority",
        ),
        (
            counts[ProfileItemKind.ACHIEVEMENT] > 0,
            _plural(counts[ProfileItemKind.ACHIEVEMENT], "achievement")
            if counts[ProfileItemKind.ACHIEVEMENT]
            else "No achievements — nothing here says what you actually did",
        ),
        (
            counts[ProfileItemKind.SKILL] > 0,
            _plural(counts[ProfileItemKind.SKILL], "skill")
            if counts[ProfileItemKind.SKILL]
            else "No skills — nothing to match against a posting's requirements",
        ),
        (
            counts[ProfileItemKind.TESTIMONIAL] > 0,
            _plural(counts[ProfileItemKind.TESTIMONIAL], "testimonial")
            if counts[ProfileItemKind.TESTIMONIAL]
            else "No testimonials — nobody else's words are in here",
        ),
        (
            criteria_set,
            "Job criteria set"
            if criteria_set
            else "No job criteria — every opening arrives unscored",
        ),
        (
            contested == 0,
            "No contested claims"
            if contested == 0
            else f"{_plural(contested, 'contested claim')} awaiting a decision",
        ),
        (
            thin == 0,
            "Every claim has corroboration"
            if thin == 0
            else f"{_plural(thin, 'claim')} resting on a single quote",
        ),
    ]
    cells = "".join(
        f'<div class="check {"yes" if ok else "no"}">'
        f'<span class="mark">{"\u2713" if ok else "\u2022"}</span>{_e(text)}</div>'
        for ok, text in rows
    )
    return f'<div class="band">{cells}</div>'


def render_profile_html(config: Config) -> str:
    """The profile page as a standalone document.

    Split out of the route so an MCP client can ask for the page and write
    it to the user's OWN machine (#284 follow-up). Claude runs locally and
    talks to wingman remotely, so the assistant holds both halves: this
    returns the markup, the local session saves it and opens it. No file
    ever has to travel, and nothing needs a capability URL pasted into a
    chat to be viewable.
    """
    # show_path=False: this document leaves the server.
    return bytes(_page("Wingman — profile", _profile_body(config, show_path=False)).body).decode(
        "utf-8"
    )


def _profile_body(config: Config, show_path: bool = True) -> str:
    """The profile page's body — shared by the route and the export tool."""
    from wingman.application.completeness import compute_completeness
    from wingman.domain.profile import ItemStatus, ProfileItemKind
    from wingman.infrastructure.storage import Storage
    from wingman.reporting.career import _reverse_chronological, _tenure

    if not config.db_path.exists():
        return _header(config, show_path) + "<h1>No workspace yet</h1>"

    # One connection for the whole render. The band needs the completeness
    # report and the page needs the items; opening a second connection to
    # answer the second question is a database round trip for nothing.
    with Storage(config.db_path) as storage:
        items = storage.list_profile_items()
        report = compute_completeness(storage, config)

    active = [i for i in items if i.status is ItemStatus.ACTIVE]
    conflicts = [i for i in items if i.status is ItemStatus.CONFLICT]
    superseded = [i for i in items if i.status is ItemStatus.SUPERSEDED]
    by_id = {i.item_id: i for i in items}

    body: list[str] = [_header(config, show_path), "<h1>Profile</h1>"]
    body.append(
        f'<p class="dim">{len(active)} active · {len(conflicts)} contested · '
        f"{len(superseded)} superseded by newer versions of their source document</p>"
    )
    body.append(_completeness_band(report, items))

    # Needs attention first: a page that buries the contested claims among
    # the settled ones is the flat document this replaces.
    attention = conflicts + [i for i in active if _claim_flags(i)]
    if attention:
        body.append('<div class="group"><div class="divider"><span>Needs attention</span></div>')
        for item in conflicts:
            rival = by_id.get(item.conflicts_with or "")
            body.append('<div class="pair">')
            body.append(_profile_item_html(item))
            if rival is not None:
                body.append('<div class="versus">conflicts with</div>')
                body.append(_profile_item_html(rival))
            body.append("</div>")
        for item in active:
            if _claim_flags(item):
                body.append(_profile_item_html(item))
        body.append("</div>")

    for heading, kind in (
        ("Roles", ProfileItemKind.ROLE),
        ("Achievements", ProfileItemKind.ACHIEVEMENT),
        ("Skills", ProfileItemKind.SKILL),
        ("Testimonials", ProfileItemKind.TESTIMONIAL),
    ):
        section = [i for i in active if i.kind is kind]
        if kind is ProfileItemKind.ROLE:
            section = _reverse_chronological(section)
        body.append(
            f'<div class="group"><div class="divider"><span>{_e(heading)}</span>'
            f'<span class="count">{len(section)}</span></div>'
        )
        if section:
            for item in section:
                tenure = _tenure(item) if kind is ProfileItemKind.ROLE else ""
                body.append(_profile_item_html(item, tenure))
        else:
            body.append('<p class="dim">Nothing yet.</p>')
        body.append("</div>")

    if superseded:
        # The lineage RFC-028 keeps and nothing ever showed. "Retired: 23"
        # is a number; these are the names behind it.
        body.append(
            '<div class="group"><div class="divider"><span>Superseded</span>'
            f'<span class="count">{len(superseded)}</span></div>'
            '<p class="dim">Replaced by newer versions of the same source document. '
            "Kept so older assessments that cite them still resolve.</p>"
        )
        for item in superseded:
            body.append(
                f'<div class="claim dim"><span class="name">{_e(item.name)}</span> '
                f'<span class="id">{_e(item.item_id[:8])}</span></div>'
            )
        body.append("</div>")

    return "\n".join(body)


async def ui_profile(request: Request) -> Response:
    """Every claim, its evidence, and what the last ingest changed."""
    config = _authorized(request)
    if config is None:
        return _not_found()
    return _page("Wingman — profile", _profile_body(config))


def normalize_prefix(prefix: str) -> str:
    """'' stays root; 'trent', '/trent', '/trent/' all become '/trent'."""
    cleaned = prefix.strip().strip("/")
    return f"/{cleaned}" if cleaned else ""


def register_ui(server: FastMCP, prefix: str = "") -> None:
    """Mount the read surface, optionally under a native path prefix.

    With prefix='/trent' the server itself listens on /trent/ui/… — for
    pass-through fronts (nginx, Caddy, direct tailnet access) that forward
    the full path. A stripping proxy (tailscale serve --set-path) must NOT
    be combined with a prefix: it removes the mount segment before
    forwarding, and the two prefixes would stack. Pages need no awareness
    either way — every URL they emit is relative. Idempotent per prefix.
    """
    mount = normalize_prefix(prefix)
    if mount in _registered_prefixes:
        return
    _registered_prefixes.add(mount)
    server.custom_route(f"{mount}/ui/{{token}}", methods=["GET"])(ui_home_redirect)
    server.custom_route(f"{mount}/ui/{{token}}/", methods=["GET"])(ui_home)
    server.custom_route(f"{mount}/ui/{{token}}/profile", methods=["GET"])(ui_profile)
    server.custom_route(f"{mount}/ui/{{token}}/file/{{path:path}}", methods=["GET"])(ui_file)
    server.custom_route(f"{mount}/ui/{{token}}/upload", methods=["POST"])(ui_upload)
    server.custom_route(f"{mount}/ui/{{token}}/keys", methods=["POST"])(ui_keys)
    # Not mounted at all under a shared multi-tenant process
    # ('configure_tenant_index' called before this runs, RFC-048) — see
    # 'ui_restart'/'_restart_panel' for why a per-tenant restart button is
    # unsafe once the process is no longer one-account-per-instance.
    if _tenant_index is None:
        server.custom_route(f"{mount}/ui/{{token}}/restart", methods=["POST"])(ui_restart)
    server.custom_route(f"{mount}/health", methods=["GET"])(ui_health)
